from uuid import uuid4
import os
import psycopg
import pytest
from psycopg.types.json import Jsonb
from core.memory.knowledge import retrieve,ingest
from core.db import pool

pytestmark=pytest.mark.integration


def test_rls_blocks_cross_tenant_even_without_where(database):
    d=database
    with d['scoped'](d['tenant'],d['support']) as conn:
        rows=conn.execute('SELECT DISTINCT tenant_id,agent_id FROM sim_records').fetchall()
        assert rows==[{'tenant_id':d['tenant'],'agent_id':d['support']}]
        assert conn.execute('SELECT * FROM agents WHERE id=%s',(d['other_support'],)).fetchone() is None


def test_agent_memory_isolation(database):
    d=database
    with d['scoped'](d['tenant'],d['ops']) as conn:
        conn.execute("INSERT INTO memory_items(id,tenant_id,agent_id,kind,content) VALUES(%s,%s,%s,'semantic',%s)",
                     (uuid4(),d['tenant'],d['ops'],Jsonb({'private':'operations only'})))
    with d['scoped'](d['tenant'],d['support']) as conn:
        rows=conn.execute('SELECT * FROM memory_items').fetchall()
        assert all(r['agent_id']==d['support'] and 'private' not in r['content'] for r in rows)


def test_rls_rejects_forged_insert(database):
    d=database
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with d['scoped'](d['tenant'],d['support']) as conn:
            conn.execute("INSERT INTO memory_items(id,tenant_id,agent_id,kind,content) VALUES(%s,%s,%s,'working','{}')",(uuid4(),d['other'],d['other_support']))


def test_vectors_prefilter_both_scopes(database):
    d=database
    with d['scoped'](d['other'],d['other_support']) as conn:
        ingest(conn,d['other'],d['other_support'],'private-customer-b','private needle needle needle refund secret')
    with d['scoped'](d['tenant'],d['ops']) as conn:
        ingest(conn,d['tenant'],d['ops'],'private-ops','private needle needle needle refund secret')
    with d['scoped'](d['tenant'],d['support']) as conn:
        rows=retrieve(conn,d['tenant'],d['support'],'private needle needle needle refund secret')
        assert all('private needle' not in r['content'] for r in rows)
        assert retrieve(conn,d['other'],d['other_support'],'private needle')==[]


def test_audit_and_policy_versions_are_immutable(database):
    d=database
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with d['scoped'](d['tenant']) as conn:
            conn.execute('DELETE FROM audit_logs')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with d['scoped'](d['tenant'],d['support']) as conn:
            conn.execute("UPDATE agent_policies SET rules='{}'")


def test_business_role_cannot_read_passwords(database):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with database['scoped'](database['tenant']) as conn:
            conn.execute('SELECT * FROM auth_private.users')


def test_api_cannot_select_other_tenant(client,database):
    assert client.get('/api/agents/'+str(database['other_support'])).status_code==404


def test_forged_scope_and_csrf_rejected(client,database):
    payload={'agent_id':str(database['ops']),'objective':'Run operations','request_key':str(uuid4()),'tenant_id':str(database['other'])}
    assert client.post('/api/runs',json=payload).status_code==422
    assert client.post('/api/runs',json={k:v for k,v in payload.items() if k!='tenant_id'},headers={'x-csrf-token':'wrong'}).status_code==403


def test_viewer_cannot_start_work(client,database):
    result=client.post('/api/auth/login',json={'email':'viewer@acme.local','password':os.environ['SEED_PASSWORD']})
    client.headers['X-CSRF-Token']=result.json()['csrf_token']
    assert client.post('/api/runs',json={'agent_id':str(database['ops']),'objective':'Run operations','request_key':str(uuid4())}).status_code==403


def test_missing_scope_is_default_deny(database):
    with pool().connection() as conn:
        with conn.transaction():
            conn.execute('SET LOCAL ROLE wf_app')
            conn.execute("SELECT set_config('app.tenant_id','',true)").fetchone()
            conn.execute("SELECT set_config('app.agent_id','',true)").fetchone()
            assert conn.execute('SELECT * FROM sim_records').fetchall()==[]
