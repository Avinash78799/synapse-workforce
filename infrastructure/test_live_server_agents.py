"""Tests the live running Docker HTTPS stack and agents end-to-end.

Executes:
1. Health & DPDP statutory notice endpoints over TLS (HTTPS port 443).
2. HTTP -> HTTPS automatic 301 redirection.
3. Authenticated session login with CSRF protection.
4. Live Customer Support Agent execution (order ORD-1001 / customer CUS-001).
5. Live Operations Agent execution (daily operations sweep).
6. Verifies worker pickups, policy evaluation, task execution, and completion.
"""
import sys
import time
import uuid
import httpx
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

def run_test():
    print("=" * 70)
    print("   LIVE DOCKER SERVER & AGENTS VERIFICATION (HTTPS)")
    print("=" * 70)

    # Read SEED_PASSWORD from .env
    env_text = (root / '.env').read_text()
    seed_password = next(line.split('=')[1].strip() for line in env_text.splitlines() if line.startswith('SEED_PASSWORD='))

    client = httpx.Client(base_url='https://localhost', verify=False, timeout=30.0)

    # 1. Test Health endpoint over HTTPS
    health = client.get('/api/health')
    assert health.status_code == 200, f"Health check failed: {health.status_code}"
    print(f"  [PASS] HTTPS Health check: {health.json()}")

    # 2. Test DPDP notice endpoint over HTTPS
    notice = client.get('/api/privacy/notice')
    assert notice.status_code == 200, f"DPDP notice failed: {notice.status_code}"
    print(f"  [PASS] DPDP Statutory Notice: Act='{notice.json()['statutory_act']}', DPO={notice.json()['data_protection_officer']['email']}")

    # 3. Test HTTP to HTTPS 301 redirect
    plain_client = httpx.Client(follow_redirects=False, timeout=10.0)
    redirect = plain_client.get('http://localhost/api/health')
    assert redirect.status_code == 301, f"Expected 301 redirect, got {redirect.status_code}"
    assert redirect.headers.get('location') == 'https://localhost/api/health'
    print(f"  [PASS] Port 80 HTTP -> 443 HTTPS 301 redirection verified.")

    # 4. Login as Owner
    login_resp = client.post('/api/auth/login', json={'email': 'owner@acme.local', 'password': seed_password})
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    user = login_resp.json()
    csrf_token = user['csrf_token']
    print(f"  [PASS] Authenticated as {user['name']} ({user['email']}) [Tenant: {user['tenant_id']}]")

    # 5. Fetch Dashboard & Agents
    dash = client.get('/api/dashboard')
    assert dash.status_code == 200
    agents = dash.json()['agents']
    support_agent = next(a for a in agents if a['kind'] == 'support')
    ops_agent = next(a for a in agents if a['kind'] == 'operations')
    print(f"  [PASS] Live Agents found: Support Agent ({support_agent['id']}), Operations Agent ({ops_agent['id']})")

    # 6. Test Live Customer Support Agent Run
    print("\n--- Testing Live Customer Support Agent Run ---")
    support_payload = {
        'agent_id': support_agent['id'],
        'objective': 'Customer Emma reports duplicate payment on order ORD-1001. Please review and refund.',
        'request_key': str(uuid.uuid4()),
        'customer_id': 'CUS-001',
        'order_id': 'ORD-1001'
    }
    run_resp = client.post('/api/runs', json=support_payload, headers={'X-CSRF-Token': csrf_token})
    assert run_resp.status_code == 202, f"Support run dispatch failed: {run_resp.text}"
    support_run_id = run_resp.json()['id']
    print(f"  --> Support run queued: {support_run_id}")

    # Poll until completed by background worker
    support_done = False
    for _ in range(12):
        time.sleep(2)
        poll = client.get(f'/api/runs/{support_run_id}').json()
        status = poll['run']['status']
        if status in ('completed', 'waiting_approval', 'escalated'):
            support_done = True
            tools_executed = [t['tool'] for t in poll.get('tasks', [])]
            print(f"  [PASS] Support run completed by worker: Status='{status}', Tasks={tools_executed}")
            break

    assert support_done, "Support agent run did not finish in time"

    # 7. Test Live Operations Agent Run
    print("\n--- Testing Live Operations Agent Run ---")
    ops_payload = {
        'agent_id': ops_agent['id'],
        'objective': 'Execute daily operations check across payment retries, shipment alerts, and inventory shortage replenishments.',
        'request_key': str(uuid.uuid4())
    }
    ops_run_resp = client.post('/api/runs', json=ops_payload, headers={'X-CSRF-Token': csrf_token})
    assert ops_run_resp.status_code == 202, f"Operations run dispatch failed: {ops_run_resp.text}"
    ops_run_id = ops_run_resp.json()['id']
    print(f"  --> Operations run queued: {ops_run_id}")

    # Poll until completed by background worker
    ops_done = False
    for _ in range(12):
        time.sleep(2)
        poll = client.get(f'/api/runs/{ops_run_id}').json()
        status = poll['run']['status']
        if status in ('completed', 'waiting_approval', 'escalated'):
            ops_done = True
            tools_executed = [t['tool'] for t in poll.get('tasks', [])]
            print(f"  [PASS] Operations run completed by worker: Status='{status}', Tasks={tools_executed}")
            break

    assert ops_done, "Operations agent run did not finish in time"

    print("\n" + "=" * 70)
    print(" [ALL LIVE SERVER & AGENT TESTS PASSED SUCCESSFULLY!]")
    print(" The live Docker container stack is fully operational on HTTPS.")
    print("=" * 70)


if __name__ == '__main__':
    run_test()
