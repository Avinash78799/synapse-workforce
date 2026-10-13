from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from core.connectors import oauth_lifecycle as life
from core.connectors import zendesk_oauth as zd
from core.security.oauth_vault import base_path
from tests.integration.test_client_foundation import business
from tests.integration.test_vendor_installations import register


@pytest.fixture
def oauth_setup(client, monkeypatch):
    c, workspace = business()
    c.base_url = 'https://testserver'
    row = register(c, workspace, 'zendesk', 'test-shop')
    store = {base_path(row)+'/oauth-client': {'client_id':'test-client','client_secret':'test-secret'}}
    class MemoryVault:
        def read(self, path):
            if path not in store: raise zd.OAuthError('oauth_vault_unavailable')
            return store[path]
        def create(self, path, value):
            assert path not in store
            store[path] = value
        def remove(self, path): store.pop(path, None)
    monkeypatch.setenv('ZENDESK_OAUTH_ENABLED', 'true')
    monkeypatch.setattr(life, 'settings', SimpleNamespace(web_origin='https://localhost'))
    from apps.api import oauth_routes
    monkeypatch.setattr(oauth_routes, 'settings', SimpleNamespace(web_origin='https://localhost'))
    monkeypatch.setattr(life, 'OAuthVault', MemoryVault)
    monkeypatch.setattr(zd, 'exchange', lambda *a, **kw: {'access_token':'test-access','refresh_token':'test-refresh',
        'scope':zd.SCOPES,'expires_in':1800,'refresh_token_expires_in':2592000})
    monkeypatch.setattr(zd, 'revoke_token', lambda *a: None)
    return c, workspace, row, store


def start(c, row):
    response = c.post('/api/workspace/vendors/'+row['id']+'/oauth/start')
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()['authorization_url']).query)
    assert query['redirect_uri'] == ['https://localhost/api/oauth/zendesk/callback']
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'samesite=lax' in response.headers['set-cookie'].lower()
    return query['state'][0]


def callback(c, state):
    return c.get('/api/oauth/zendesk/callback', params={'state':state,'code':'private-code'}, follow_redirects=False)


def test_authorize_replay_refresh_and_revoke(oauth_setup, database, monkeypatch):
    c, w, row, store = oauth_setup
    state = start(c, row)
    response = callback(c, state)
    assert response.status_code == 303 and response.headers['location'].endswith('connected_read_only')
    assert callback(c, state).headers['location'].endswith('authorization_incomplete')
    with database['scoped'](w['tenant']['id']) as conn:
        conn.execute('UPDATE vendor_oauth_connections SET refresh_due_at=now()-interval \'1 second\' WHERE installation_id=%s', (row['id'],))
    calls = []
    def exchange(*a, **kw):
        calls.append(kw)
        return {'access_token':'new-access','refresh_token':'new-refresh','scope':zd.SCOPES,'expires_in':1800,'refresh_token_expires_in':2592000}
    monkeypatch.setattr(zd, 'exchange', exchange)
    life.refresh_due(w['tenant']['id'],row['id'])
    life.refresh_due(w['tenant']['id'],row['id'])
    assert len(calls) == 1 and calls[0]['refresh_token'] == 'test-refresh'
    assert c.post('/api/workspace/vendors/'+row['id']+'/oauth/revoke').json()['status'] == 'revoked'
    assert c.post('/api/workspace/vendors/'+row['id']+'/check').status_code == 409
    with database['scoped'](w['tenant']['id']) as conn:
        assert 'test-access' not in str(conn.execute('SELECT * FROM audit_logs').fetchall())
        assert 'test-refresh' not in str(conn.execute('SELECT * FROM vendor_oauth_connections').fetchall())


def test_callback_requires_browser_binding_and_live_original_session(oauth_setup, database):
    c, w, row, store = oauth_setup
    state = start(c, row)
    browser = c.cookies.get('wf_oauth'); c.cookies.delete('wf_oauth')
    assert callback(c,state).headers['location'].endswith('authorization_incomplete')
    c.cookies.set('wf_oauth', browser, path='/api/oauth/zendesk/callback')
    assert c.post('/api/auth/logout').status_code == 200
    assert callback(c,state).headers['location'].endswith('authorization_incomplete')


def test_tenant_isolation_and_owner_permission(oauth_setup):
    c, w, row, store = oauth_setup
    other, _ = business(email='other-oauth@new.example')
    assert other.post('/api/workspace/vendors/'+row['id']+'/oauth/start').status_code == 409
    assert other.get('/api/workspace/vendors/'+row['id']+'/oauth').json()['connection'] is None


def test_uncertain_refresh_never_repeats_grant(oauth_setup,database,monkeypatch):
    c,w,row,store = oauth_setup
    callback(c,start(c,row))
    with database['scoped'](w['tenant']['id']) as conn:
        conn.execute('UPDATE vendor_oauth_connections SET refresh_due_at=now()-interval \'1 second\' WHERE installation_id=%s',(row['id'],))
    calls=[]
    def exchange(*a,**kw):
        calls.append(1); raise zd.OAuthError('oauth_outcome_unknown')
    monkeypatch.setattr(zd,'exchange',exchange)
    life.refresh_due(w['tenant']['id'],row['id']);life.refresh_due(w['tenant']['id'],row['id'])
    assert calls==[1]
    assert c.get('/api/workspace/vendors/'+row['id']+'/oauth').json()['connection']['status']=='reauth_required'


@pytest.mark.native_postgres
def test_concurrent_callbacks_exchange_once(oauth_setup,monkeypatch):
    c,w,row,store = oauth_setup
    state=start(c,row);browser=c.cookies.get('wf_oauth');calls=[]
    def exchange(*a,**kw):
        calls.append(1)
        return {'access_token':'a','refresh_token':'r','scope':zd.SCOPES,'expires_in':1800,'refresh_token_expires_in':2592000}
    monkeypatch.setattr(zd,'exchange',exchange)
    def attempt():
        try:return life.accept_callback(state,browser,'code')
        except zd.OAuthError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:attempt(),range(2)))
    assert results.count('connected_read_only')==1 and len(calls)==1
