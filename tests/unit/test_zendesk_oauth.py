import json
from urllib.parse import parse_qs, urlsplit
import httpx
import pytest
from core.connectors import zendesk_oauth as oauth
from core.security import oauth_vault as vault

REAL_CLIENT = httpx.Client
CLIENT = {'client_id': 'test-client', 'client_secret': 'secret-client'}


def bundle():
    return {'access_token': 'secret-access', 'refresh_token': 'secret-refresh', 'token_type': 'bearer',
            'scope': oauth.SCOPES, 'expires_in': 1800, 'refresh_token_expires_in': 2592000}


def mock_http(monkeypatch, handler):
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, 'Client', client)


def test_pkce_and_server_account_binding():
    verifier, challenge = oauth.pkce()
    assert 43 <= len(verifier) <= 128 and verifier != challenge
    url = urlsplit(oauth.authorization_url('my-shop', CLIENT['client_id'], oauth.callback_uri('https://localhost'), 'state', challenge))
    query = parse_qs(url.query)
    assert url.hostname == 'my-shop.zendesk.com'
    assert query['code_challenge_method'] == ['S256']
    assert query['scope'] == [oauth.SCOPES]
    assert 'client_secret' not in query
    assert verifier not in url.geturl()
    with pytest.raises(ValueError):
        oauth.authorization_url('my-shop.evil.com', 'id', 'https://localhost', 'state', challenge)


@pytest.mark.parametrize('origin', ['http://localhost', 'https://good@evil.com', 'https://a/b', 'https://a/?q=1', 'https://a/#fragment'])
def test_callback_is_fixed_https_origin(origin):
    with pytest.raises(oauth.OAuthError):
        oauth.callback_uri(origin)


def test_code_and_refresh_requests_are_read_only(monkeypatch):
    sent = []
    def handler(request):
        assert request.url == 'https://shop.zendesk.com/oauth/tokens'
        body = json.loads(request.content); sent.append(body)
        assert body['scope'] == oauth.SCOPES
        return httpx.Response(201, json=bundle())
    mock_http(monkeypatch, handler)
    oauth.exchange('shop', CLIENT, code='code', verifier='verifier', redirect_uri=oauth.callback_uri('https://localhost'))
    oauth.exchange('shop', CLIENT, refresh_token='refresh')
    assert sent[0]['grant_type'] == 'authorization_code' and sent[0]['code_verifier'] == 'verifier'
    assert sent[1]['grant_type'] == 'refresh_token' and 'code' not in sent[1]


@pytest.mark.parametrize('status', [302, 429, 500, 502])
def test_unknown_grants_are_never_retried_or_leaked(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text='private-vendor-payload')
    mock_http(monkeypatch, handler)
    with pytest.raises(oauth.OAuthError, match='^oauth_outcome_unknown$'):
        oauth.exchange('shop', CLIENT, refresh_token='secret')
    assert len(calls) == 1


def test_timeout_is_unknown_without_retry(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('private-token')
    mock_http(monkeypatch, handler)
    with pytest.raises(oauth.OAuthError, match='^oauth_outcome_unknown$'):
        oauth.exchange('shop', CLIENT, refresh_token='secret')
    assert len(calls) == 1


@pytest.mark.parametrize('field,value', [('scope','read write'), ('scope','tickets:read'), ('refresh_token',''),
    ('expires_in',True), ('expires_in',0), ('expires_in','1800'), ('refresh_token_expires_in',None), ('token_type','basic')])
def test_incomplete_or_excessive_grants_fail_closed(field, value):
    data = bundle(); data[field] = value
    with pytest.raises(oauth.OAuthError):
        oauth.token_bundle(data)


def test_revocation_requires_vendor_confirmation(monkeypatch):
    def handler(request):
        assert request.method == 'DELETE'
        assert request.url.path == '/api/v2/oauth/tokens/current.json'
        return httpx.Response(401)
    mock_http(monkeypatch, handler)
    with pytest.raises(oauth.OAuthError, match='revocation_unconfirmed'):
        oauth.revoke_token('shop', 'test')


def test_vault_cas_and_no_secret_fallback(monkeypatch, tmp_path):
    from types import SimpleNamespace
    token_file = tmp_path/'vault-token'; token_file.write_text('test-only-vault-token')
    monkeypatch.setattr(vault, 'settings', SimpleNamespace(vault_addr='https://vault.example', app_env='production', vault_token_file=str(token_file)))
    def handler(request):
        assert request.headers['X-Vault-Token'] == 'test-only-vault-token'
        assert request.url.host == 'vault.example'
        if request.method == 'POST':
            assert json.loads(request.content)['options'] == {'cas': 0}
            return httpx.Response(200, json={'data': {'version': 1}})
        return httpx.Response(403, text='secret-diagnostics')
    mock_http(monkeypatch, handler)
    vault.OAuthVault().create('tenants/test/tokens/1', {'value': 'test'})
    with pytest.raises(oauth.OAuthError, match='^oauth_vault_unavailable$'):
        vault.OAuthVault().read('tenants/test/tokens/1')
    with pytest.raises(oauth.OAuthError):
        vault.OAuthVault().read('../other-tenant')


def test_production_vault_rejects_plain_http(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(vault, 'settings', SimpleNamespace(vault_addr='http://vault:8200', app_env='production'))
    with pytest.raises(oauth.OAuthError, match='oauth_vault_https_required'):
        vault.OAuthVault().read('a/b')
