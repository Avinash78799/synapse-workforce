import os
from uuid import uuid4
import pytest
from core.runtime.runs import process_run


@pytest.mark.parametrize('database', ['zendesk_ticket_triage'], indirect=True)
def test_default_demo_asks_before_escalation_and_never_refunds(client, database):
    agents = client.get('/api/dashboard').json()['agents']
    assert len(agents) == 1 and agents[0]['autonomy'] == 3
    agent = agents[0]['id']
    response = client.post('/api/runs', json={'agent_id':agent, 'objective':'Triage this payment complaint',
        'request_key':str(uuid4()), 'customer_id':'CUS-001', 'order_id':'ORD-1001'})
    assert response.status_code == 202, response.text
    run = response.json()['id']
    process_run(database['tenant'], agent, run)
    detail = client.get('/api/runs/'+run).json()
    assert detail['run']['status'] == 'waiting_approval'
    assert [t['tool'] for t in detail['tasks']] == ['support.investigate', 'ticket.update']
    trust = client.get('/api/trust').json()
    assert trust['pending_approvals'] == 1 and len(trust['actions']) == 2
    assert any(a['permission']=='Waiting for a person' for a in trust['actions'])
    approval = client.get('/api/approvals').json()[0]
    login = client.post('/api/auth/login', json={'email':'finance_approver@acme.local', 'password':os.environ['SEED_PASSWORD']})
    client.headers['X-CSRF-Token'] = login.json()['csrf_token']
    assert client.post('/api/approvals/'+approval['id']+'/decision', json={'decision':'approved', 'reason':'Review fictional triage'}).status_code == 200
    process_run(database['tenant'], agent, run)
    process_run(database['tenant'], agent, run)
    assert client.get('/api/runs/'+run).json()['run']['status'] == 'escalated'
    with database['scoped'](database['tenant'], agent) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sim_records WHERE kind IN ('refund','message')").fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) AS n FROM tool_receipts WHERE task_id=%s", (detail['tasks'][1]['id'],)).fetchone()['n'] == 1
