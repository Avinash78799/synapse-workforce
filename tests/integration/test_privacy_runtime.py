"""Real SQL and workflow regressions; require disposable TEST_DATABASE_URL."""
import os
from uuid import uuid4
import pytest
from psycopg.types.json import Jsonb
from core.privacy.consent import withdraw_consent
from core.privacy.rights import execute_erasure, export_data_principal_summary, PrivacyConflict
from core.runtime.runs import process_run
from tests.integration.test_workflows import start
from tests.integration.test_hardening import login

pytestmark=pytest.mark.integration


def queue(client,d,number=1):
    response=client.post('/api/runs',json={'agent_id':str(d['support']),'objective':'Resolve duplicate charge','request_key':str(uuid4()),'customer_id':f'CUS-{number:03}','order_id':f'ORD-{1000+number}'})
    assert response.status_code==202,response.text
    return response.json()


def test_withdrawal_before_worker_blocks_model(client,database,monkeypatch):
    run=queue(client,database)
    with database['scoped'](database['tenant']) as conn:
        withdraw_consent(conn,database['tenant'],'CUS-001')
    monkeypatch.setattr('core.runtime.runs.get_model',lambda:pytest.fail('Model called after withdrawal'))
    process_run(database['tenant'],database['support'],run['id'])
    assert client.get('/api/runs/'+run['id']).json()['run']['status']=='failed'


def test_withdrawal_overrides_approved_refund(client,database):
    detail=start(client,database,2)
    approval=client.get('/api/approvals').json()[0]
    login(client,'finance_approver@acme.local')
    assert client.post('/api/approvals/'+approval['id']+'/decision',json={'decision':'approved','reason':'Independent review complete'}).status_code==200
    with database['scoped'](database['tenant']) as conn:
        withdraw_consent(conn,database['tenant'],'CUS-002')
    process_run(database['tenant'],database['support'],detail['run']['id'])
    with database['scoped'](database['tenant'],database['support']) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sim_records WHERE kind='refund'").fetchone()['n']==0
        assert conn.execute("SELECT status FROM agent_tasks WHERE run_id=%s AND tool='payment.refund'",(detail['run']['id'],)).fetchone()['status']=='denied'


def test_erasure_uses_real_schema_and_preserves_other_tenant(database):
    d=database
    from core.memory.knowledge import ingest
    with d['scoped'](d['tenant'],d['support']) as conn:
        conn.execute("INSERT INTO memory_items(id,tenant_id,agent_id,kind,content) VALUES(%s,%s,%s,'working',%s)",(uuid4(),d['tenant'],d['support'],Jsonb({'customer_id':'CUS-001','email':'customer1@example.test'})))
        document=ingest(conn,d['tenant'],d['support'],'Customer note','CUS-001 customer1@example.test')
    with d['scoped'](d['tenant']) as conn:
        summary=export_data_principal_summary(conn,d['tenant'],'CUS-001')
        assert summary['sim_records_count']>2
        assert summary['sub_processors']==[]
        result=execute_erasure(conn,d['tenant'],'CUS-001')
        assert result['status']=='processing' and result['operational_scrub']=='completed'
        assert result['memory_purged']==1 and result['documents_purged']==1
        assert conn.execute('SELECT 1 FROM knowledge_chunks WHERE document_id=%s',(document,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM sim_records WHERE kind='customer' AND id='CUS-001' AND data->>'email'='customer1@example.test'").fetchone() is None
        assert conn.execute("SELECT status FROM privacy_requests WHERE id=%s",(result['request_id'],)).fetchone()['status']=='processing'
    with d['scoped'](d['other'],d['other_support']) as conn:
        assert conn.execute("SELECT data FROM sim_records WHERE kind='customer' AND id='CUS-001'").fetchone()['data']['email']=='customer1@example.test'


def test_erasure_refuses_active_work(client,database):
    queue(client,database)
    with database['scoped'](database['tenant']) as conn:
        with pytest.raises(PrivacyConflict,match='active'): execute_erasure(conn,database['tenant'],'CUS-001')


def test_privacy_routes_reject_viewer(client,database):
    login(client,'viewer@acme.local')
    for path,body in [('/privacy/consent',{'data_principal_id':'CUS-001','purpose':'support.order_resolution'}),('/privacy/consent/withdraw',{'data_principal_id':'CUS-001'}),('/privacy/dsar',{'data_principal_id':'CUS-001'}),('/privacy/erase',{'data_principal_id':'CUS-001'})]:
        assert client.post('/api'+path,json=body).status_code==403


def test_timeout_freezes_refund_and_blocks_customer_message(client,database,monkeypatch):
    from core.connectors.simulation import SimulationConnector
    class Uncertain(SimulationConnector):
        def execute(self,*a): raise RuntimeError('Provider response lost')
    from core.runtime import executor
    original=executor.get_connector
    monkeypatch.setattr(executor,'get_connector',lambda name:Uncertain() if name=='payment.refund' else original(name))
    detail=start(client,database)
    refund=next(t for t in detail['tasks'] if t['tool']=='payment.refund')
    assert refund['verification']=='unknown'
    assert detail['tasks'][-1]['status']=='blocked'
    with database['scoped'](database['tenant'],database['support']) as conn:
        assert conn.execute('SELECT state FROM tool_receipts WHERE task_id=%s',(refund['id'],)).fetchone()['state']=='unknown'


def test_embedding_identity_excludes_legacy_rows(database):
    from core.memory.knowledge import retrieve
    d=database
    with d['scoped'](d['tenant'],d['support']) as conn:
        assert retrieve(conn,d['tenant'],d['support'],'refund')
        conn.execute("UPDATE knowledge_chunks SET embedding_identity='legacy:unverified'")
        assert retrieve(conn,d['tenant'],d['support'],'refund')==[]
