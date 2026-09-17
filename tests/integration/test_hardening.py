import copy
import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
import psycopg
from core.runtime.runs import process_run
from core.runtime.audit import verify_export,verification_keys
from tests.integration.test_workflows import start

pytestmark=pytest.mark.integration


def login(client,email):
    r=client.post('/api/auth/login',json={'email':email,'password':os.environ['SEED_PASSWORD']})
    assert r.status_code==200
    client.headers['X-CSRF-Token']=r.json()['csrf_token']
    return r


def test_owner_self_decision_is_blocked_and_independent_finance_resumes(client,database):
    run=start(client,database,2)['run']
    approval=client.get('/api/approvals').json()[0]
    assert approval['independent_required']
    path='/api/approvals/'+approval['id']+'/decision'
    for decision in ['approved','denied']:
        assert client.post(path,json={'decision':decision,'reason':'Requester wants to decide'}).status_code==403
    assert client.get('/api/approvals').json()[0]['status']=='pending'
    assert client.get('/api/security/signals').json()[0]['details']['signal']=='self_approval_attempt'
    login(client,'finance_approver@acme.local')
    assert client.post(path,json={'decision':'approved','reason':'Independent financial review'}).status_code==200
    process_run(database['tenant'],database['support'],run['id'])
    assert client.get('/api/runs/'+run['id']).json()['run']['status']=='completed'


def test_pending_independence_cannot_be_relaxed_by_policy_update(client,database):
    start(client,database,2)
    aid=str(database['support'])
    rules=client.get('/api/agents/'+aid).json()['policy']['rules']
    rules.update(independent_approval_threshold_minor=100_000_000,auto_refund_limit_minor=100_000_000)
    assert client.post('/api/agents/'+aid+'/policies',json=rules).status_code==200
    approval=client.get('/api/approvals').json()[0]
    assert client.post('/api/approvals/'+approval['id']+'/decision',json={'decision':'approved','reason':'Try relaxed policy'}).status_code==403


def test_role_attempts_are_logged_and_throttled(client,database):
    start(client,database,2)
    approval=client.get('/api/approvals').json()[0]
    login(client,'operator@acme.local')
    path='/api/approvals/'+approval['id']+'/decision'
    for _ in range(5):
        assert client.post(path,json={'decision':'approved','reason':'Unauthorized role attempt'}).status_code==403
    assert client.post(path,json={'decision':'denied','reason':'Another unauthorized attempt'}).status_code==429
    assert client.get('/api/security/signals').status_code==403
    login(client,'owner@acme.local')
    assert any(r['details']['signal']=='role_escalation_attempt' for r in client.get('/api/security/signals').json())


def test_approval_burst_limit_includes_duplicate_decisions(client,database):
    start(client,database,3)
    aid=client.get('/api/approvals').json()[0]['id']
    path='/api/approvals/'+aid+'/decision'
    assert client.post(path,json={'decision':'denied','reason':'Deny repeated claim'}).status_code==200
    for _ in range(19):
        assert client.post(path,json={'decision':'denied','reason':'Repeated submission'}).status_code==409
    assert client.post(path,json={'decision':'denied','reason':'Repeated submission'}).status_code==429


@pytest.mark.parametrize('offset',[0,330,-300])
def test_expiry_exact_boundary_uses_utc(client,database,monkeypatch,offset):
    start(client,database,2)
    login(client,'finance_approver@acme.local')
    deadline=datetime(2030,1,1,12,tzinfo=timezone.utc)
    with database['scoped'](database['tenant'],database['support']) as conn:
        conn.execute('UPDATE approval_requests SET expires_at=%s',(deadline.astimezone(timezone(timedelta(minutes=offset))),))
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls,tz=None):
            return deadline.astimezone(tz or timezone.utc)
    import apps.api.main as api
    monkeypatch.setattr(api,'datetime',FrozenDatetime)
    aid=client.get('/api/approvals').json()[0]['id']
    r=client.post('/api/approvals/'+aid+'/decision',json={'decision':'approved','reason':'Decision exactly at expiry'})
    assert r.json()['status']=='expired'


def test_relogin_rotates_and_invalidates_previous_session(client,database):
    from fastapi.testclient import TestClient
    from apps.api.main import app
    old=client.cookies.get('wf_session')
    login(client,'owner@acme.local')
    assert client.cookies.get('wf_session')!=old
    replay=TestClient(app)
    replay.cookies.set('wf_session',old)
    assert replay.get('/api/auth/me').status_code==401
    replay.close()


def test_logout_token_cannot_be_replayed(client,database):
    token=client.cookies.get('wf_session')
    assert client.post('/api/auth/logout').status_code==200
    client.cookies.set('wf_session',token)
    assert client.get('/api/auth/me').status_code==401


def test_csrf_token_cannot_cross_sessions(client,database):
    from fastapi.testclient import TestClient
    from apps.api.main import app
    other=TestClient(app)
    r=other.post('/api/auth/login',json={'email':'owner@acme.local','password':os.environ['SEED_PASSWORD']})
    old_csrf=client.headers['X-CSRF-Token']
    other.headers['X-CSRF-Token']=old_csrf
    assert other.post('/api/auth/logout').status_code==403
    other.headers['X-CSRF-Token']=r.json()['csrf_token']
    assert other.post('/api/auth/logout').status_code==200
    other.close()


def test_skill_cap_restricts_refund_without_elevating_agent(client,database):
    path='/api/agents/'+str(database['support'])+'/skills/RefundSkill/autonomy'
    assert client.put(path,json={'cap':5}).status_code==422
    assert client.put(path,json={'cap':2}).status_code==200
    detail=start(client,database)
    assert detail['tasks'][1]['status']=='prepared'
    with database['scoped'](database['tenant'],database['support']) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sim_records WHERE kind='refund'").fetchone()['n']==0


def freeze_refund(client,database):
    detail=start(client,database)
    task=detail['tasks'][1]
    with database['scoped'](database['tenant'],database['support']) as conn:
        conn.execute("UPDATE tool_receipts SET state='unknown' WHERE task_id=%s",(task['id'],))
        conn.execute("UPDATE agent_tasks SET status='failed',verification='unknown' WHERE id=%s",(task['id'],))
    return task


def test_unknown_review_reads_evidence_without_redispatch(client,database):
    task=freeze_refund(client,database)
    assert client.get('/api/reconciliation').json()[0]['id']==task['id']
    r=client.post('/api/reconciliation/'+task['id']+'/resolve',json={'choice':'verify','reason':'Read back the provider refund ledger'})
    assert r.status_code==200,r.text
    assert r.json()['status']=='passed' and not r.json()['resumed']
    assert client.get('/api/reconciliation').json()==[]
    with database['scoped'](database['tenant'],database['support']) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sim_records WHERE kind='refund'").fetchone()['n']==1
        assert conn.execute('SELECT dispatch_count FROM tool_receipts WHERE task_id=%s',(task['id'],)).fetchone()['dispatch_count']==1


def test_unknown_missing_evidence_stays_frozen_then_closes_unresolved(client,database):
    task=freeze_refund(client,database)
    with database['scoped'](database['tenant'],database['support']) as conn:
        conn.execute("DELETE FROM sim_records WHERE kind='refund'")
    path='/api/reconciliation/'+task['id']+'/resolve'
    assert client.post(path,json={'choice':'verify','reason':'Cannot find refund in ledger'}).status_code==409
    assert len(client.get('/api/reconciliation').json())==1
    assert client.post(path,json={'choice':'close_unresolved','reason':'Escalated for manual provider investigation'}).status_code==200
    assert client.get('/api/reconciliation').json()==[]


def test_reconciliation_rejects_viewer_and_other_tenant(client,database):
    task=freeze_refund(client,database)
    login(client,'viewer@acme.local')
    path='/api/reconciliation/'+task['id']+'/resolve'
    assert client.post(path,json={'choice':'verify','reason':'Attempt without review role'}).status_code==403
    login(client,'owner@northstar.local')
    assert client.get('/api/reconciliation').json()==[]
    assert client.post(path,json={'choice':'verify','reason':'Wrong tenant review attempt'}).status_code==404


def test_audit_export_verifies_and_detects_changed_payload(client,database):
    start(client,database)
    r=client.get('/api/audit/export')
    assert r.status_code==200,r.text
    exported=r.json()
    assert verify_export(exported,verification_keys())['signed_events']>5
    changed=copy.deepcopy(exported)
    changed['events'][0]['result']='forged'
    with pytest.raises(ValueError):
        verify_export(changed,verification_keys())
    with psycopg.connect(database['url']) as conn:
        conn.execute("UPDATE audit_logs SET result='forged' WHERE id=%s",(exported['events'][0]['id'],))
    assert client.get('/api/audit/export').status_code==409


def test_external_checkpoint_detects_deleted_tail(client,database):
    start(client,database)
    first=client.get('/api/audit/export').json()
    # A privileged DBA can truncate a tail, but cannot make it match the retained checkpoint.
    last=first['events'][-1]['id']
    with psycopg.connect(database['url']) as conn:
        conn.execute('DELETE FROM audit_logs WHERE id=%s',(last,))
    second=client.get('/api/audit/export').json()
    with pytest.raises(ValueError,match='retained checkpoint'):
        verify_export(second,verification_keys(),first)


def test_budget_reports_reservations_and_actuals_separately(client,database):
    from apps.api.main import redis
    start(client,database)
    day=datetime.now(timezone.utc).date()
    redis.set(f"workforce:model-budget:{database['tenant']}:{day}",2000000)
    r=client.get('/api/budgets')
    assert r.status_code==200,r.text
    assert r.json()['reserved_usd']==2 and float(r.json()['recorded_cost_usd'])==0
    assert r.json()['provider']=='simulation'


def test_vault_has_no_reusable_cache_and_hard_abort_blocks_lookup(client,database,monkeypatch):
    from core.security.secrets import VaultSecrets
    ref=uuid4()
    with psycopg.connect(database['url']) as conn:
        integration=conn.execute('SELECT id FROM integrations WHERE agent_id=%s LIMIT 1',(database['support'],)).fetchone()[0]
        conn.execute('INSERT INTO credentials(id,tenant_id,agent_id,integration_id,secret_ref) VALUES(%s,%s,%s,%s,%s)',(uuid4(),database['tenant'],database['support'],integration,ref))
    vault=VaultSecrets();reads=[]
    monkeypatch.setattr(vault,'read',lambda path:reads.append(path) or 'test-secret')
    with database['scoped'](database['tenant'],database['support']) as conn:
        for _ in range(2):vault.integration_key(conn,database['tenant'],database['support'],ref)
    assert len(reads)==2
    client.post('/api/agents/'+str(database['support'])+'/lifecycle',json={'state':'hard_aborted','reason':'Revoke all credential access'})
    with database['scoped'](database['tenant'],database['support']) as conn:
        with pytest.raises(PermissionError):vault.integration_key(conn,database['tenant'],database['support'],ref)
    assert len(reads)==2
