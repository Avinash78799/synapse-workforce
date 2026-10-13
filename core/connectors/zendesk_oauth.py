"""Read-only Zendesk authorization contracts. Never retry token exchanges."""
import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode, urlsplit
import httpx
from core.connectors.vendor_readers import hostname

SCOPES = 'tickets:read users:read'
CALLBACK_PATH = '/api/oauth/zendesk/callback'


class OAuthError(RuntimeError):
    """Only a controlled error code may leave this module."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


def callback_uri(origin):
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise OAuthError('oauth_https_origin_required')
    return origin.rstrip('/') + CALLBACK_PATH


def client_config(value):
    if not isinstance(value, dict):
        raise OAuthError('oauth_client_unavailable')
    for key in ('client_id', 'client_secret'):
        if (not isinstance(value.get(key), str) or not value[key] or len(value[key]) > 4096
                or any(c.isspace() for c in value[key])):
            raise OAuthError('oauth_client_unavailable')
    return {key: value[key] for key in ('client_id', 'client_secret')}


def authorization_url(account, client_id, redirect_uri, state, challenge):
    return 'https://' + hostname('zendesk', account) + '/oauth/authorizations/new?' + urlencode({
        'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri,
        'scope': SCOPES, 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})


def token_bundle(data):
    if not isinstance(data, dict):
        raise OAuthError('oauth_invalid_response')
    for field in ('access_token', 'refresh_token'):
        value = data.get(field)
        if not isinstance(value, str) or not value or len(value) > 8192 or any(c.isspace() for c in value):
            raise OAuthError('oauth_invalid_response')
    if str(data.get('token_type', '')).lower() != 'bearer':
        raise OAuthError('oauth_invalid_response')
    if not isinstance(data.get('scope'), str) or set(data['scope'].split()) != set(SCOPES.split()):
        raise OAuthError('oauth_scope_mismatch')
    for field, low, high in (('expires_in', 300, 172800), ('refresh_token_expires_in', 604800, 7776000)):
        value = data.get(field)
        if type(value) is not int or not low <= value <= high:
            raise OAuthError('oauth_expiry_missing_or_invalid')
    return {k: data[k] for k in ('access_token', 'refresh_token', 'scope', 'expires_in', 'refresh_token_expires_in')}


def exchange(account, client, *, code=None, verifier=None, redirect_uri=None, refresh_token=None):
    body = {**client_config(client), 'scope': SCOPES, 'expires_in': 1800, 'refresh_token_expires_in': 2592000}
    if refresh_token:
        body.update(grant_type='refresh_token', refresh_token=refresh_token)
    elif code and verifier and redirect_uri:
        body.update(grant_type='authorization_code', code=code, code_verifier=verifier, redirect_uri=redirect_uri)
    else:
        raise OAuthError('oauth_invalid_grant_request')
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False) as http:
            with http.stream('POST', 'https://' + hostname('zendesk', account) + '/oauth/tokens', json=body) as response:
                if response.status_code in (400, 401, 403):
                    raise OAuthError('oauth_grant_rejected')
                # A timeout, 429, redirect or 5xx never proves that a rotating grant was unused.
                if response.status_code not in (200, 201):
                    raise OAuthError('oauth_outcome_unknown')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise OAuthError('oauth_invalid_response')
                return token_bundle(json.loads(raw))
    except OAuthError:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise OAuthError('oauth_outcome_unknown') from None


def revoke_token(account, token):
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False) as http:
            response = http.delete('https://' + hostname('zendesk', account) + '/api/v2/oauth/tokens/current.json',
                                   headers={'Authorization': 'Bearer ' + token})
            if response.status_code != 204:
                raise OAuthError('oauth_revocation_unconfirmed')
    except httpx.HTTPError:
        raise OAuthError('oauth_revocation_unconfirmed') from None
