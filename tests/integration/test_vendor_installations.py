from uuid import uuid4
import pytest
from apps.api import vendor_routes as routes
from core.connectors.vendor_readers import VendorError
from tests.integration.test_client_foundation import business


def register(c,w,vendor='shopify',account='test-store'):
    r=c.post('/api/workspace/vendors',json={'vendor':vendor,'account':account,'agent_id':w['agents'][0]['id']})
    assert r.status_code==201,r.text
    return r.json()


def test_installation_scope_credentials_and_revocation(client,database,monkeypatch):
    c,w=business();other,ow=business(email='other@new.example');row=register(c,w)
    assert row['status']=='pending_credentials' and not row['workflow_execution_enabled']
    assert str(w['tenant']['id']) in row['credential_path'] and row['agent_id'] in row['credential_path']
    assert other.get('/api/workspace/vendors').json()['installations']==[]
    for suffix in ('/check','/inspect'):
        assert other.post('/api/workspace/vendors/'+row['id']+suffix,json={'resource_id':'42'}).status_code==404
    assert other.delete('/api/workspace/vendors/'+row['id']).status_code==404
    assert c.post('/api/workspace/vendors',json={'vendor':'shopify','account':'foreign','agent_id':ow['agents'][0]['id']}).status_code==404
    assert c.post('/api/workspace/vendors',json={'vendor':'shopify','account':'test-store','agent_id':row['agent_id']}).status_code==409
    reads=[]
    def credential(self,conn,tenant,agent,reference):
        assert str(tenant)==w['tenant']['id'] and str(agent)==row['agent_id'] and str(reference)==row['secret_ref']
        reads.append(reference);return 'private-vendor-token'
    monkeypatch.setattr(routes.VaultSecrets,'integration_key',credential)
    monkeypatch.setattr(routes,'check_account',lambda *args:{'account_read':True,'write_actions_enabled':False})
    assert c.post('/api/workspace/vendors/'+row['id']+'/check').status_code==200
    listing=c.get('/api/workspace/vendors')
    assert listing.json()['installations'][0]['status']=='read_check_passed' and 'private-vendor-token' not in listing.text
    assert c.delete('/api/workspace/vendors/'+row['id']).status_code==200
    assert c.post('/api/workspace/vendors/'+row['id']+'/check').status_code==409
    assert len(reads)==1
    with database['scoped'](w['tenant']['id'],row['agent_id']) as conn:
        assert conn.execute('SELECT count(*) AS n FROM agent_runs').fetchone()['n']==0
    assert register(c,w)['id']!=row['id']


@pytest.mark.parametrize('code,status',[('credential_unavailable',503),('vendor_rate_limited',503),('credential_or_scope_denied',502)])
def test_failed_checks_persist_only_sanitized_evidence(client,database,monkeypatch,code,status):
    c,w=business();row=register(c,w,'zendesk')
    monkeypatch.setattr(routes.VaultSecrets,'integration_key',lambda *args:'private-token')
    def fail(*args):raise VendorError(code)
    monkeypatch.setattr(routes,'check_account',fail)
    r=c.post('/api/workspace/vendors/'+row['id']+'/check')
    assert r.status_code==status and r.json()['detail']==code
    saved=c.get('/api/workspace/vendors').json()['installations'][0]
    assert saved['status']=='check_failed' and saved['last_check']=={'code':code,'operation':'account'}


def test_input_authority_and_paused_agent(client,database,monkeypatch):
    c,w=business();agent=w['agents'][0]['id']
    base={'vendor':'shopify','account':'acme','agent_id':agent}
    for extra in ({'secret_ref':str(uuid4())},{'tenant_id':str(database['other'])},{'account':'http://localhost'},{'vendor':'custom'}):
        assert c.post('/api/workspace/vendors',json={**base,**extra}).status_code==422
    row=register(c,w)
    with database['scoped'](w['tenant']['id'],agent) as conn:
        conn.execute("UPDATE agents SET status='draining' WHERE id=%s",(agent,))
    monkeypatch.setattr(routes.VaultSecrets,'integration_key',lambda *args:pytest.fail('Paused agent accessed credentials'))
    assert c.post('/api/workspace/vendors/'+row['id']+'/check').status_code==409
    for resource in ('0','-1','42/../1','1'*21):
        assert c.post('/api/workspace/vendors/'+row['id']+'/inspect',json={'resource_id':resource}).status_code==422


def test_owner_only_inspection_and_database_agent_scope(client,database,monkeypatch):
    import psycopg
    c,w=business();row=register(c,w)
    other_agent=next(a['id'] for a in w['agents'] if a['id']!=row['agent_id'])
    with database['scoped'](w['tenant']['id'],other_agent) as conn:
        assert conn.execute('SELECT * FROM vendor_installations').fetchall()==[]
    monkeypatch.setattr(routes.VaultSecrets,'integration_key',lambda *args:'test-token')
    def resource(vendor,account,token,number):
        assert (vendor,account,token,number)==('shopify','test-store','test-token','42')
        return {'resource':{'id':'gid://shopify/Order/42'},'workflow_execution_enabled':False}
    monkeypatch.setattr(routes,'read_resource',resource)
    assert c.post('/api/workspace/vendors/'+row['id']+'/inspect',json={'resource_id':'42'}).status_code==200
    # A resource read alone must not promote an account to a passed scope check.
    assert c.get('/api/workspace/vendors').json()['installations'][0]['status']=='pending_credentials'
    with psycopg.connect(database['url']) as conn:
        conn.execute("UPDATE auth_private.users SET role='viewer' WHERE tenant_id=%s",(w['tenant']['id'],))
    assert c.get('/api/workspace/vendors').status_code==403
    assert c.post('/api/workspace/vendors',json={'vendor':'shopify','account':'test','agent_id':row['agent_id']}).status_code==403
    assert c.delete('/api/workspace/vendors/'+row['id']).status_code==403
    assert c.post('/api/workspace/vendors/'+row['id']+'/check').status_code==403
