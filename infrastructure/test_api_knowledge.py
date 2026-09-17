import httpx
from pathlib import Path

env_text = Path('.env').read_text(encoding='utf-8')
password = next(line.split('=')[1].strip() for line in env_text.splitlines() if line.startswith('SEED_PASSWORD='))

client = httpx.Client(base_url='https://localhost', verify=False, timeout=10.0)
login = client.post('/api/auth/login', json={'email': 'owner@acme.local', 'password': password}).json()
headers = {'X-CSRF-Token': login['csrf_token']}

dash = client.get('/api/dashboard', headers=headers).json()
support = next(a for a in dash['agents'] if a['kind'] == 'support')

resp = client.post(f"/api/agents/{support['id']}/knowledge/query", json={'query': 'When is duplicate payment eligible for refund?'}, headers=headers).json()
print("Knowledge Query Result:")
print("Sources count:", len(resp.get('sources', [])))
if resp.get('sources'):
    print("Citation:", resp['sources'][0].get('citation'))
    print("Snippet:", resp['sources'][0].get('text')[:100], "...")
    assert len(resp['sources']) > 0
    assert '[Doc: ' in resp['sources'][0].get('citation')
    print("API KNOWLEDGE RETRIEVAL VERIFIED SUCCESSFULLY!")
