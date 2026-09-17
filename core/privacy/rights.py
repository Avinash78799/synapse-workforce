"""Scoped operational privacy controls. Retained evidence requires separate review."""
import json
import re
from uuid import uuid4
from datetime import datetime, timezone
from psycopg.types.json import Jsonb
from core.security.pii import anonymize_identifier
from core.privacy.consent import consent_lock, withdraw_consent


class PrivacyConflict(ValueError):
    pass


def _records(conn, tenant_id, principal_id):
    rows = conn.execute('SELECT agent_id, kind, id, data FROM sim_records WHERE tenant_id=%s', (tenant_id,)).fetchall()
    selected = {(str(r['agent_id']), r['kind'], r['id']) for r in rows
                if (r['kind'] == 'customer' and r['id'] == principal_id) or r['data'].get('customer_id') == principal_id}
    # Follow order/payment/ticket links separately inside each agent's dataset.
    for _ in range(4):
        ids = {(a, i) for a, _, i in selected}
        more = {(str(r['agent_id']), r['kind'], r['id']) for r in rows
                if any((str(r['agent_id']), r['data'].get(k)) in ids for k in ('order_id','payment_id','ticket_id'))}
        if more <= selected:
            break
        selected |= more
    return [r for r in rows if (str(r['agent_id']), r['kind'], r['id']) in selected]


def export_data_principal_summary(conn, tenant_id, principal_id):
    records = _records(conn, tenant_id, principal_id)
    consents = conn.execute('SELECT purpose,status,notice_version,granted_at,withdrawn_at FROM data_consents WHERE tenant_id=%s AND data_principal_id=%s', (tenant_id, principal_id)).fetchall()
    summary = {'data_principal_id': principal_id, 'tenant_id': str(tenant_id),
               'generated_at': datetime.now(timezone.utc).isoformat(),
               'consents': json.loads(json.dumps([dict(c) for c in consents], default=str)),
               'sim_records_count': len(records), 'records_summary': [dict(r) for r in records],
               'sub_processors': [], 'processor_disclosure_status': 'Transfer-level inventory not yet implemented; do not infer no transfers',
               'coverage': 'Scoped business records and consent history; retained audit and external copies require review'}
    conn.execute("""INSERT INTO privacy_requests(id,tenant_id,data_principal_id,request_type,status,sla_due_at,details,result_summary,created_at,completed_at)
        VALUES(%s,%s,%s,'access_summary','processing',now()+interval '30 days',%s,'Operational export ready; transfer inventory review required',now(),NULL)""",
        (uuid4(),tenant_id,principal_id,Jsonb({'records_count':len(records), 'requires_review':True})))
    return summary


def execute_erasure(conn, tenant_id, principal_id):
    consent_lock(conn, tenant_id, exclusive=True)
    busy = conn.execute("SELECT 1 FROM agent_runs WHERE tenant_id=%s AND status NOT IN ('completed','escalated','failed','aborted') LIMIT 1", (tenant_id,)).fetchone()
    if busy:
        raise PrivacyConflict('Finish or abort active tenant runs before erasure; withdraw consent to block further dispatch')
    records = _records(conn, tenant_id, principal_id)
    if not records:
        raise PrivacyConflict('No scoped business records found for this principal')
    withdraw_consent(conn, tenant_id, principal_id)
    anon = anonymize_identifier(str(tenant_id) + ':' + principal_id)
    pii_keys = {'name','email','phone','address','shipping_address','billing_address','first_name','last_name'}
    personal_values = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in pii_keys and isinstance(item,str) and len(item)>2:
                    personal_values.add(item)
                collect(item)
        elif isinstance(value,list):
            for item in value: collect(item)
    for row in records: collect(row['data'])
    def scrub(value):
        if isinstance(value,dict):
            return {k: '[ERASED]' if k in pii_keys else scrub(v) for k,v in value.items()}
        if isinstance(value,list): return [scrub(v) for v in value]
        if isinstance(value,str):
            for token in sorted(personal_values,key=len,reverse=True): value=value.replace(token,'[ERASED]')
        return value
    for row in records:
        data = scrub(row['data'])
        # Keep stable customer/order references for consent enforcement and deduplication.
        if row['kind']=='customer': data.update(name='ANONYMIZED_'+anon, privacy_erased=True)
        conn.execute('UPDATE sim_records SET data=%s WHERE tenant_id=%s AND agent_id=%s AND kind=%s AND id=%s',
                     (Jsonb(data),tenant_id,row['agent_id'],row['kind'],row['id']))
    identifiers = {principal_id} | {r['id'] for r in records} | personal_values
    pattern = re.compile('|'.join(re.escape(t) for t in sorted(identifiers,key=len,reverse=True)))
    def matches(value): return bool(pattern.search(json.dumps(value,default=str)))
    memory_count = 0
    for row in conn.execute('SELECT id,content FROM memory_items WHERE tenant_id=%s',(tenant_id,)).fetchall():
        if matches(row['content']):
            conn.execute('DELETE FROM memory_items WHERE tenant_id=%s AND id=%s',(tenant_id,row['id']))
            memory_count += 1
    document_count = 0
    for row in conn.execute('SELECT id,title,content FROM knowledge_documents WHERE tenant_id=%s',(tenant_id,)).fetchall():
        if matches(row):
            conn.execute('DELETE FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s',(tenant_id,row['id']))
            conn.execute('DELETE FROM knowledge_documents WHERE tenant_id=%s AND id=%s',(tenant_id,row['id']))
            document_count += 1
    # Scrub free text in completed run data; leave immutable action arguments and
    # signed audit chains intact for evidence review instead of invalidating them.
    for row in conn.execute('SELECT id,objective,plan,summary,error FROM agent_runs WHERE tenant_id=%s',(tenant_id,)).fetchall():
        if matches(row):
            conn.execute('UPDATE agent_runs SET objective=%s,plan=%s,summary=%s,error=%s WHERE tenant_id=%s AND id=%s',
                (scrub(row['objective']),Jsonb(scrub(row['plan'])),Jsonb(scrub(row['summary'])),scrub(row['error']),tenant_id,row['id']))
    request_id=uuid4()
    result={'request_id':str(request_id),'data_principal_id':principal_id,'anonymized_reference':anon,
            'records_anonymized':len(records),'memory_purged':memory_count,'documents_purged':document_count,
            'status':'processing','operational_scrub':'completed',
            'remaining_review':['Signed audit and action evidence','Privacy request history','External processors and backups','Unlinked or unidentified personal data'],
            'notice':'Operational scrub completed. Full erasure requires retention and external-copy review.'}
    conn.execute("""INSERT INTO privacy_requests(id,tenant_id,data_principal_id,request_type,status,sla_due_at,details,result_summary,created_at,completed_at)
        VALUES(%s,%s,%s,'erasure','processing',now()+interval '30 days',%s,'Operational scrub completed; retention review pending',now(),NULL)""",
        (request_id,tenant_id,principal_id,Jsonb(result)))
    return result
