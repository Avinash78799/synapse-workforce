"""Vault KV v2 only; no environment fallback for OAuth material."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from core.config import settings
from core.connectors.zendesk_oauth import OAuthError


class OAuthVault:
    def _request(self, method, reference, data=None, area='data'):
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', reference):
            raise OAuthError('oauth_vault_reference_invalid')
        parsed = urlsplit(settings.vault_addr)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise OAuthError('oauth_vault_configuration_invalid')
        if settings.app_env.lower() in ('production', 'prod') and parsed.scheme != 'https':
            raise OAuthError('oauth_vault_https_required')
        try:
            token = Path(settings.vault_token_file).read_text().strip()
            if not token:
                raise OAuthError('oauth_vault_unavailable')
            with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False) as http:
                with http.stream(method, settings.vault_addr.rstrip('/') + '/v1/secret/' + area + '/' + reference,
                                 headers={'X-Vault-Token': token}, **({'json': data} if data is not None else {})) as response:
                    if response.status_code not in (200, 204):
                        raise OAuthError('oauth_vault_unavailable')
                    if response.status_code == 204:
                        return {}
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            raise OAuthError('oauth_vault_unavailable')
                    return json.loads(raw)
        except OAuthError:
            raise
        except (OSError, httpx.HTTPError, ValueError, TypeError):
            raise OAuthError('oauth_vault_unavailable') from None

    def read(self, reference):
        try:
            value = self._request('GET', reference)['data']['data']
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (KeyError, TypeError, ValueError):
            raise OAuthError('oauth_vault_unavailable') from None

    def create(self, reference, value):
        # Every attempt and token generation has an immutable, unique path.
        self._request('POST', reference, {'options': {'cas': 0}, 'data': value})

    def remove(self, reference):
        # Remove all versions, not just the latest soft-deleted value.
        self._request('DELETE', reference, area='metadata')


def base_path(row):
    return f"tenants/{row['tenant_id']}/agents/{row['agent_id']}/integrations/{row['secret_ref']}"


def token_path(row):
    return base_path(row) + '/oauth-tokens/' + str(row['generation'])
