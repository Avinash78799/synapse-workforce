"""HMAC-authenticated per-agent audit chains. Signing keys never live in SQL.

External retention of exported checkpoints is required to detect tail deletion.
This is tamper evidence, not write-once storage or a public-key signature.
"""
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from psycopg.types.json import Jsonb

ZERO = '0' * 64
FIELDS = ('id','tenant_id','agent_id','actor','agent_version','run_id','task_id',
          'action','tool','policy_version','approval_id','result','details',
          'created_at','chain_seq','previous_hash','key_id')


def canonical(value):
    def encode(v):
        if isinstance(v, datetime):
            return v.astimezone(timezone.utc).isoformat()
        return str(v)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=encode,
                      ensure_ascii=True, allow_nan=False).encode()


def signing_key():
    key=Path(os.getenv('AUDIT_SIGNING_KEY_FILE','/run/secrets/audit_signing_key')).read_bytes().strip()
    if len(key)<32:
        raise RuntimeError('Audit signing key must have at least 32 bytes')
    return key


def key_id(key):
    return hashlib.sha256(key).hexdigest()[:16]


def verification_keys():
    key=signing_key()
    keys={key_id(key):key}
    for name in filter(None,os.getenv('AUDIT_VERIFY_KEY_FILES','').split(os.pathsep)):
        previous=Path(name).read_bytes().strip()
        keys[key_id(previous)]=previous
    return keys


def audit(conn, tenant, agent, action, result, *, actor='system', run=None, task=None,
          tool=None, policy='platform-v1', approval=None, version='1.0.0', details=None):
    key=signing_key()
    # Per-agent lock fits the RLS scope; independent agents have separate chains.
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,7))',(str(tenant)+':'+str(agent),))
    previous=conn.execute('''SELECT chain_seq,event_hash FROM audit_logs
        WHERE tenant_id=%s AND agent_id IS NOT DISTINCT FROM %s AND chain_seq IS NOT NULL
        ORDER BY chain_seq DESC LIMIT 1''',(tenant,agent)).fetchone()
    event={'id':conn.execute("SELECT nextval('audit_logs_id_seq') AS id").fetchone()['id'],
           'tenant_id':tenant,'agent_id':agent,'actor':str(actor),'agent_version':version,
           'run_id':run,'task_id':task,'action':action,'tool':tool,'policy_version':str(policy),
           'approval_id':approval,'result':result,'details':details or {},
           'created_at':datetime.now(timezone.utc),'chain_seq':previous['chain_seq']+1 if previous else 1,
           'previous_hash':previous['event_hash'] if previous else ZERO,'key_id':key_id(key)}
    event_hash=hashlib.sha256(canonical(event)).hexdigest()
    signature=hmac.new(key,event_hash.encode(),'sha256').hexdigest()
    values=[Jsonb(event[f]) if f=='details' else event[f] for f in FIELDS]
    conn.execute('INSERT INTO audit_logs('+','.join(FIELDS)+',event_hash,signature) VALUES('
                 +','.join(['%s']*(len(FIELDS)+2))+')',values+[event_hash,signature])


def verify_rows(rows, keys):
    heads={}
    legacy=0
    for row in sorted(rows,key=lambda r:int(r['id'])):
        if row['chain_seq'] is None:
            legacy+=1
            continue
        chain=str(row['tenant_id'])+':'+str(row['agent_id'])
        seq,previous=heads.get(chain,(0,ZERO))
        if row['chain_seq']!=seq+1 or row['previous_hash']!=previous:
            raise ValueError('Audit chain is incomplete or reordered')
        calculated=hashlib.sha256(canonical({f:row[f] for f in FIELDS})).hexdigest()
        key=keys.get(row['key_id'])
        if not key or not hmac.compare_digest(calculated,row['event_hash']) or not hmac.compare_digest(
                hmac.new(key,calculated.encode(),'sha256').hexdigest(),row['signature']):
            raise ValueError('Audit signature verification failed')
        heads[chain]=(row['chain_seq'],row['event_hash'])
    return {'signed_events':len(rows)-legacy,'legacy_unsigned_events':legacy,
            'heads':{chain:{'sequence':v[0],'hash':v[1]} for chain,v in heads.items()}}


def export_audit(conn, tenant):
    rows=conn.execute('SELECT * FROM audit_logs ORDER BY id').fetchall()
    evidence=verify_rows(rows,verification_keys())
    key=signing_key()
    document={'format':'workforce-audit-v1','tenant_id':str(tenant),'exported_at':datetime.now(timezone.utc),
              'key_id':key_id(key),'verification':evidence,'events':rows,
              'retention_notice':'Retain this export independently. Tail deletion requires a prior trusted checkpoint to detect.'}
    signature=hmac.new(key,canonical(document),'sha256').hexdigest()
    return json.loads(canonical({**document,'export_hmac':signature}))


def verify_export(document, keys, previous=None):
    unsigned={k:v for k,v in document.items() if k!='export_hmac'}
    key=keys.get(document['key_id'])
    if not key or not hmac.compare_digest(hmac.new(key,canonical(unsigned),'sha256').hexdigest(),document['export_hmac']):
        raise ValueError('Export authentication failed')
    evidence=verify_rows(document['events'],keys)
    if evidence!=document['verification']:
        raise ValueError('Export checkpoint mismatch')
    if previous:
        verify_export(previous,keys)
        if previous['tenant_id']!=document['tenant_id']:
            raise ValueError('Checkpoints belong to different tenants')
        current={(str(e['tenant_id'])+':'+str(e['agent_id']),e['chain_seq']):e['event_hash']
                 for e in document['events'] if e['chain_seq'] is not None}
        for chain,head in previous['verification']['heads'].items():
            if current.get((chain,head['sequence']))!=head['hash']:
                raise ValueError('History differs from the retained checkpoint')
    return evidence
