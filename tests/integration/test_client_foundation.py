from dataclasses import replace
from uuid import uuid4
import pytest
import psycopg
from fastapi.testclient import TestClient
from apps.api.main import app

PASSWORD = 'sandbox-test-password-2026'


def business(email='owner@new.example', category='ecommerce'):
    c = TestClient(app)
    r = c.post('/api/workspaces', json=dict(business_name='New Business', owner_name='New Owner', email=email, password=PASSWORD, category=category))
    assert r.status_code == 201, r.text
    login = c.post('/api/auth/login', json=dict(email=email, password=PASSWORD))
    assert login.status_code == 200, login.text
    c.headers['X-CSRF-Token'] = login.json()['csrf_token']
    w = c.get('/api/workspace').json()
    return c, w


def connect(c):
    for category in ('ecommerce', 'custom', 'crm'):
        r = c.post(f'/api/workspace/connections/{category}/connect', json={'load_demo_data': True})
        assert r.status_code == 200, r.text


def key(c, w, scopes=None, agent=None, **extra):
    agent = agent or next(a['id'] for a in w['agents'] if a['kind'] == 'support')
    r = c.post('/api/workspace/api-keys', json=dict(name='Backend key', scopes=scopes or ['runs:read', 'runs:write', 'integrations:read'], agent_ids=[agent], **extra))
    assert r.status_code == 201, r.text
    api = TestClient(app, headers={'Authorization': 'Bearer '+r.json()['token']})
    return api, r.json()


def payload(w):
    return dict(agent_id=next(a['id'] for a in w['agents'] if a['kind']=='support'), request_key=str(uuid4()), objective='Investigate duplicate charge', customer_id='CUS-001', order_id='ORD-1001')


@pytest.mark.parametrize('category', ['ecommerce', 'custom', 'crm'])
def test_signup_is_empty_isolated_and_atomic(client, database, category):
    c, w = business(category=category)
    assert w['profile']['category'] == category
    assert len(w['agents']) == 2
    assert all(a['autonomy'] == 3 for a in w['agents'])
    assert all(x['status'] == 'disconnected' for x in w['connections'])
    with database['scoped'](w['tenant']['id']) as conn:
        assert conn.execute('SELECT count(*) AS n FROM sim_records').fetchone()['n'] == 0
        assert len(conn.execute('SELECT * FROM tenants').fetchall()) == 1
    with psycopg.connect(database['url']) as conn:
        before = conn.execute('SELECT count(*) FROM tenants').fetchone()[0]
    duplicate = TestClient(app).post('/api/workspaces', json=dict(business_name='Duplicate',owner_name='Owner',email='owner@new.example',password=PASSWORD,category=category))
    assert duplicate.status_code == 409
    with psycopg.connect(database['url']) as conn:
        assert conn.execute('SELECT count(*) FROM tenants').fetchone()[0] == before
    assert c.post('/api/workspace/connections/crm/connect',json={'load_demo_data':False}).status_code == 422
    connect(c)
    assert c.post('/api/workspace/connections/crm/test').json()['records_available']


def test_keys_idempotency_isolation_rotation_and_sandbox_runtime(client, database, monkeypatch):
    c, w = business()
    api, credential = key(c,w)
    body = payload(w)
    assert api.post('/api/v1/runs',json=body).status_code == 409
    connect(c)
    first = api.post('/api/v1/runs',json=body)
    assert first.status_code == 202, first.text
    run_id = first.json()['run_id']
    replay = api.post('/api/v1/runs',json=body)
    assert replay.json()['replayed'] and replay.json()['run_id'] == run_id
    assert api.post('/api/v1/runs',json={**body,'objective':'Different request'}).status_code == 409
    assert api.get('/api/workspace').status_code == 401
    assert api.get('/api/v1/integrations',headers={'Origin':'https://localhost'}).status_code == 403
    assert 'token' not in c.get('/api/workspace/api-keys').json()[0]
    assert credential['token'] not in c.get('/api/workspace/api-keys').text
    other, ow = business('owner@other.example')
    other_api, _ = key(other,ow)
    assert other_api.get('/api/v1/runs/'+run_id).status_code == 404
    assert other.delete('/api/workspace/api-keys/'+credential['id']).status_code == 404
    assert other.post('/api/workspace/api-keys',json=dict(name='Wrong agent',scopes=['runs:read'],agent_ids=[body['agent_id']])).status_code == 422
    ops = next(a['id'] for a in w['agents'] if a['kind']=='operations')
    ops_api, _ = key(c,w,agent=ops)
    assert ops_api.get('/api/v1/runs/'+run_id).status_code == 403
    readonly, _ = key(c,w,scopes=['runs:read'])
    assert readonly.post('/api/v1/runs',json=body).status_code == 403
    from core.runtime import runs
    from core.runtime import executor
    from core.memory import knowledge
    monkeypatch.setattr(runs,'get_model',lambda:pytest.fail('Sandbox used external model selection'))
    monkeypatch.setattr(executor,'get_connector',lambda *args:pytest.fail('Sandbox used global connector selection'))
    monkeypatch.setattr(knowledge,'settings',replace(knowledge.settings,embedding_provider='external'))
    monkeypatch.setattr(knowledge,'get_embedding_provider',lambda:pytest.fail('Sandbox used external embeddings'))
    runs.process_run(w['tenant']['id'],body['agent_id'],run_id)
    result = api.get('/api/v1/runs/'+run_id)
    assert result.status_code == 200, result.text
    assert result.json()['run']['status'] == 'waiting_approval', result.text
    rotated = c.post('/api/workspace/api-keys/'+credential['id']+'/rotate')
    assert rotated.status_code == 201, rotated.text
    assert api.get('/api/v1/integrations').status_code == 401
    api.headers['Authorization'] = 'Bearer '+rotated.json()['token']
    assert api.get('/api/v1/integrations').status_code == 200
    c.delete('/api/workspace/api-keys/'+rotated.json()['id'])
    assert api.get('/api/v1/integrations').status_code == 401
    c.delete('/api/workspace/connections/crm')
    from core.runtime.executor import connector_for
    with database['scoped'](w['tenant']['id'],body['agent_id']) as conn:
        with pytest.raises(ValueError): connector_for(conn,'ticket.update')


def test_invitation_one_use_revoke_and_role_limits(client, database):
    c, w = business()
    invitation = c.post('/api/workspace/invitations',json={'email':'viewer@new.example','role':'viewer'})
    assert invitation.status_code == 201, invitation.text
    body = dict(token=invitation.json()['token'],name='Viewer',password=PASSWORD)
    guest = TestClient(app)
    assert guest.post('/api/invitations/accept',json=body).status_code == 201
    assert guest.post('/api/invitations/accept',json=body).status_code == 409
    login = guest.post('/api/auth/login',json={'email':'viewer@new.example','password':PASSWORD})
    guest.headers['X-CSRF-Token'] = login.json()['csrf_token']
    assert guest.get('/api/workspace').json()['tenant']['id'] == w['tenant']['id']
    assert guest.get('/api/workspace/members').status_code == 403
    assert guest.post('/api/workspace/connections/crm/connect',json={'load_demo_data':True}).status_code == 403
    assert guest.post('/api/workspace/invitations',json={'email':'bad@new.example','role':'operator'}).status_code == 403
    invitation = c.post('/api/workspace/invitations',json={'email':'revoked@new.example','role':'operator'}).json()
    assert c.delete('/api/workspace/invitations/'+invitation['id']).status_code == 200
    assert TestClient(app).post('/api/invitations/accept',json={**body,'token':invitation['token']}).status_code == 409


def test_signup_switch_and_key_rate_limit(client, database, monkeypatch):
    monkeypatch.setenv('WORKSPACE_SIGNUP_ENABLED','false')
    assert TestClient(app).get('/api/onboarding/options').json()['signup_enabled'] is False
    assert TestClient(app).post('/api/workspaces',json=dict(business_name='Business',owner_name='Owner',email='no@new.example',password=PASSWORD,category='crm')).status_code == 403
    monkeypatch.setenv('WORKSPACE_SIGNUP_ENABLED','true')
    c,w = business()
    api, _ = key(c,w,rate_per_minute=1)
    assert api.get('/api/v1/integrations').status_code == 200
    assert api.get('/api/v1/integrations').status_code == 429


def test_expired_keys_and_shared_submission_limit(client, database, monkeypatch):
    from apps.api import client_routes
    from core.security.passwords import token_hash
    c,w = business()
    connect(c)
    api, credential = key(c,w)
    another, _ = key(c,w)
    monkeypatch.setattr(client_routes,'settings',replace(client_routes.settings,max_runs_per_hour=1))
    body = payload(w)
    assert api.post('/api/v1/runs',json=body).status_code == 202
    assert another.post('/api/v1/runs',json=body).json()['replayed']
    assert another.post('/api/v1/runs',json={**body,'request_key':str(uuid4()),'order_id':'ORD-1002','customer_id':'CUS-002'}).status_code == 429
    with psycopg.connect(database['url']) as conn:
        stored = conn.execute('SELECT token_hash FROM auth_private.client_api_keys WHERE id=%s',(credential['id'],)).fetchone()[0]
        assert stored == token_hash(credential['token']) and stored != credential['token']
        conn.execute("UPDATE auth_private.client_api_keys SET expires_at=now()-interval '1 second' WHERE id=%s",(credential['id'],))
    assert api.get('/api/v1/integrations').status_code == 401


def test_concurrent_requests_create_one_submission(client, database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    c,w = business()
    connect(c)
    api, credential = key(c,w)
    body = payload(w)
    barrier = Barrier(2)
    def send(_):
        with TestClient(app,headers={'Authorization':'Bearer '+credential['token']}) as caller:
            barrier.wait()
            return caller.post('/api/v1/runs',json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send,range(2)))
    assert all(r.status_code==202 for r in responses)
    assert responses[0].json()['run_id']==responses[1].json()['run_id']
    assert sorted(r.json()['replayed'] for r in responses)==[False,True]
    with database['scoped'](w['tenant']['id'],body['agent_id']) as conn:
        assert conn.execute('SELECT count(*) AS n FROM api_submissions').fetchone()['n']==1
