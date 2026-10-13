from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import time
from threading import Barrier
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from apps.api.main import app
from tests.integration.test_client_foundation import business, connect, PASSWORD


def endpoint(client, workspace, kind='support'):
    agent = next(a['id'] for a in workspace['agents'] if a['kind'] == kind)
    response = client.post('/api/workspace/webhooks', json={'name':'Website events','agent_id':agent})
    assert response.status_code == 201, response.text
    return response.json()


def event(kind='support.duplicate_charge', **changes):
    data = {'event_id':str(uuid4()),'event_type':kind}
    if kind == 'support.duplicate_charge':
        data.update(customer_id='CUS-001',order_id='ORD-1001')
    return {**data, **changes}


def delivery(hook, data=None, raw=None, timestamp=None, secret=None, headers=None):
    raw = raw if raw is not None else json.dumps(data,separators=(',',':')).encode()
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    signature = 'v1='+hmac.new((secret or hook['secret']).encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    with TestClient(app) as sender:
        return sender.post(hook['path'],content=raw,headers={
            'Content-Type':'application/json','X-Workforce-Timestamp':timestamp,
            'X-Workforce-Signature':signature,**(headers or {})})


def test_signed_delivery_retries_binding_and_approval(client,database):
    c,w = business();connect(c);hook = endpoint(c,w);body = event()
    first = delivery(hook,body)
    assert first.status_code == 202,first.text
    run = first.json()['run_id']
    replay = delivery(hook,body)
    assert replay.status_code == 202 and replay.json()['replayed']
    assert replay.json()['run_id'] == run
    assert delivery(hook,{**body,'order_id':'ORD-1002'}).status_code == 409
    from core.runtime.runs import process_run
    process_run(w['tenant']['id'],hook['agent_id'],run)
    detail = c.get('/api/runs/'+run).json()
    assert detail['run']['status'] == 'waiting_approval'
    history = c.get('/api/workspace/webhooks/'+hook['id']+'/events')
    row = history.json()[0]
    assert row['outcome']=='accepted' and row['attempts']==2 and row['run_id']==run
    assert hook['secret'] not in history.text+c.get('/api/workspace/webhooks').text
    assert 'customer_id' not in history.text and 'payload_digest' not in history.text


@pytest.mark.parametrize('attack',['tamper','past','future','signature','wrong_key','origin','schema','big','encoding'])
def test_untrusted_deliveries_do_not_create_runs(client,database,attack):
    c,w = business();connect(c);hook = endpoint(c,w);body=event()
    kwargs={}
    expected=401
    if attack=='tamper':
        # A valid signature for another exact body must fail.
        timestamp=str(int(time.time()))
        raw=json.dumps(body,separators=(',',':')).encode()
        signature='v1='+hmac.new(hook['secret'].encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        kwargs.update(timestamp=timestamp,headers={'X-Workforce-Signature':signature})
        body['customer_id']='CUS-002'
    if attack=='past':kwargs['timestamp']=int(time.time())-301
    if attack=='future':kwargs['timestamp']=int(time.time())+301
    if attack=='signature':kwargs['headers']={'X-Workforce-Signature':'bad'}
    if attack=='wrong_key':kwargs['secret']='whsec_wrong'
    if attack=='origin':kwargs['headers']={'Origin':'https://localhost'};expected=403
    if attack=='schema':body['tenant_id']=str(database['other']);expected=422
    if attack=='big':kwargs['raw']=b' '*32769;expected=413
    if attack=='encoding':kwargs['headers']={'Content-Encoding':'gzip'};expected=415
    response=delivery(hook,body,**kwargs)
    assert response.status_code==expected,response.text
    with database['scoped'](w['tenant']['id']) as conn:
        assert conn.execute('SELECT count(*) AS n FROM agent_runs').fetchone()['n']==0
        assert conn.execute('SELECT count(*) AS n FROM webhook_events').fetchone()['n']==0


def test_rejection_history_can_recover_with_same_event(client,database):
    c,w=business();hook=endpoint(c,w);body=event()
    response=delivery(hook,body)
    assert response.status_code==409 and response.json()['detail']=='sandbox_connections_required'
    rows=c.get('/api/workspace/webhooks/'+hook['id']+'/events').json()
    assert rows[0]['outcome']=='rejected' and rows[0]['run_id'] is None
    connect(c)
    assert delivery(hook,body).status_code==202
    rows=c.get('/api/workspace/webhooks/'+hook['id']+'/events').json()
    assert rows[0]['outcome']=='accepted' and rows[0]['attempts']==2
    bad_binding=delivery(hook,event(customer_id='CUS-002'))
    assert bad_binding.status_code==409 and bad_binding.json()['detail']=='customer_order_binding_invalid'


def test_endpoint_owner_scope_rotation_revocation_and_master_change(client,database,monkeypatch,tmp_path):
    c,w=business();connect(c);hook=endpoint(c,w)
    other,ow=business('owner@other.example')
    assert other.post('/api/workspace/webhooks',json={'name':'Wrong agent','agent_id':hook['agent_id']}).status_code==422
    for suffix in ('/rotate',''):
        response=other.post('/api/workspace/webhooks/'+hook['id']+suffix) if suffix else other.delete('/api/workspace/webhooks/'+hook['id'])
        assert response.status_code==404
    assert other.get('/api/workspace/webhooks/'+hook['id']+'/events').status_code==404
    other_hook=endpoint(other,ow)
    assert delivery(hook,event(),secret=other_hook['secret']).status_code==401
    rotated=c.post('/api/workspace/webhooks/'+hook['id']+'/rotate').json()
    assert delivery(hook,event()).status_code==401
    assert delivery(rotated,event()).status_code==202
    new_master=tmp_path/'replacement';new_master.write_text('a-different-webhook-master-key-with-32-plus-bytes')
    monkeypatch.setenv('WEBHOOK_SIGNING_KEY_FILE',str(new_master))
    assert delivery(rotated,event()).status_code==503
    assert c.get('/api/workspace/webhooks').json()['endpoints'][0]['needs_rotation']
    rotated=c.post('/api/workspace/webhooks/'+hook['id']+'/rotate').json()
    assert delivery(rotated,event()).status_code==202
    assert c.delete('/api/workspace/webhooks/'+hook['id']).status_code==200
    assert delivery(rotated,event()).status_code==401


def test_viewer_cannot_manage_and_missing_signing_key_fails_closed(client,database,monkeypatch):
    c,w=business();hook=endpoint(c,w)
    invite=c.post('/api/workspace/invitations',json={'email':'viewer@new.example','role':'viewer'}).json()
    viewer=TestClient(app)
    assert viewer.post('/api/invitations/accept',json={'token':invite['token'],'name':'Viewer','password':PASSWORD}).status_code==201
    login=viewer.post('/api/auth/login',json={'email':'viewer@new.example','password':PASSWORD})
    viewer.headers['X-CSRF-Token']=login.json()['csrf_token']
    assert viewer.post('/api/workspace/webhooks',json={'name':'Denied','agent_id':hook['agent_id']}).status_code==403
    assert viewer.post('/api/workspace/webhooks/'+hook['id']+'/rotate').status_code==403
    assert viewer.delete('/api/workspace/webhooks/'+hook['id']).status_code==403
    assert viewer.get('/api/workspace/webhooks/'+hook['id']+'/events').status_code==200
    monkeypatch.setenv('WEBHOOK_SIGNING_KEY_FILE','/missing/webhook-master')
    assert not c.get('/api/workspace/webhooks').json()['signing_configured']
    assert c.post('/api/workspace/webhooks',json={'name':'Unavailable','agent_id':hook['agent_id']}).status_code==503
    assert delivery(hook,event()).status_code==503


def test_concurrent_webhooks_queue_once_and_store_no_secret(client,database):
    c,w=business();connect(c);hook=endpoint(c,w);body=event();barrier=Barrier(2)
    def send(_):
        barrier.wait();return delivery(hook,body)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,range(2)))
    assert all(r.status_code==202 for r in results)
    assert len({r.json()['run_id'] for r in results})==1
    with database['scoped'](w['tenant']['id']) as conn:
        assert conn.execute('SELECT count(*) AS n FROM agent_runs').fetchone()['n']==1
        assert conn.execute('SELECT count(*) AS n FROM queue_outbox').fetchone()['n']==1
        assert conn.execute('SELECT attempts FROM webhook_events').fetchone()['attempts']==2
    from core.db import auth_db
    with auth_db() as conn:
        row=conn.execute('SELECT * FROM auth_private.webhook_endpoints WHERE id=%s',(hook['id'],)).fetchone()
        assert hook['secret'] not in str(row)


def test_failed_enqueue_rolls_back_receipt_and_run(client,database,monkeypatch):
    c,w=business();connect(c);hook=endpoint(c,w)
    from core.runtime import runs
    def broken(*args):raise RuntimeError('Simulated outbox failure')
    monkeypatch.setattr(runs,'enqueue',broken)
    with pytest.raises(RuntimeError,match='outbox failure'):delivery(hook,event())
    with database['scoped'](w['tenant']['id']) as conn:
        assert conn.execute('SELECT count(*) AS n FROM agent_runs').fetchone()['n']==0
        assert conn.execute('SELECT count(*) AS n FROM webhook_events').fetchone()['n']==0


def test_operations_type_binding_and_workflow(client,database):
    c,w=business();connect(c);hook=endpoint(c,w,'operations')
    assert delivery(hook,event()).json()['detail']=='event_agent_mismatch'
    response=delivery(hook,event('operations.review'))
    assert response.status_code==202,response.text
    from core.runtime.runs import process_run
    process_run(w['tenant']['id'],hook['agent_id'],response.json()['run_id'])
    assert c.get('/api/runs/'+response.json()['run_id']).json()['run']['status']=='waiting_approval'


def test_webhooks_share_agent_run_limit_and_replay_remains_safe(client,database,monkeypatch):
    from dataclasses import replace
    from apps.api import webhook_routes
    c,w=business();connect(c);hook=endpoint(c,w);body=event()
    monkeypatch.setattr(webhook_routes,'settings',replace(webhook_routes.settings,max_runs_per_hour=1))
    assert delivery(hook,body).status_code==202
    assert delivery(hook,body).json()['replayed']
    response=delivery(hook,event(customer_id='CUS-002',order_id='ORD-1002'))
    assert response.status_code==429
    assert len(c.get('/api/workspace/webhooks/'+hook['id']+'/events').json())==1


def test_local_master_key_setup_preserves_existing_secret(tmp_path,monkeypatch):
    from infrastructure import configure_webhooks
    monkeypatch.setattr(configure_webhooks,'__file__',str(tmp_path/'infrastructure'/'configure_webhooks.py'))
    configure_webhooks.configure()
    path=tmp_path/'.secrets'/'webhook_signing_key'
    first=path.read_bytes()
    assert len(first)>=32
    configure_webhooks.configure()
    assert path.read_bytes()==first
