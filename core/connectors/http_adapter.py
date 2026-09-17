"""Authenticated HTTP bridge. Remote evidence never falls back to simulation."""
import hashlib
import hmac
import json
from urllib.parse import urlsplit
import httpx
from core.config import settings
from core.connectors.base import BaseConnector


class ConnectorUnavailable(RuntimeError):
    """No authoritative provider outcome is available; do not retry a mutation."""


class HttpConnector(BaseConnector):
    def __init__(self, base_url=None, secret=None, timeout=15.0):
        self._base_url, self._secret, self.timeout = base_url, secret, timeout

    @property
    def base_url(self):
        return (self._base_url or settings.external_connector_base_url or '').rstrip('/')

    @property
    def secret(self):
        return self._secret or settings.external_connector_secret or ''

    def _headers(self, idempotency_key, body_bytes):
        if not self.secret:
            raise ConnectorUnavailable('External connector signing secret is required')
        return {'Content-Type': 'application/json', 'Idempotency-Key': idempotency_key,
                'X-Signature-SHA256': hmac.new(self.secret.encode(), body_bytes, hashlib.sha256).hexdigest()}

    def _scope(self, conn):
        row = conn.execute("SELECT current_setting('app.tenant_id',true) AS tenant_id, current_setting('app.agent_id',true) AS agent_id").fetchone()
        if not row or not row['tenant_id'] or not row['agent_id'] or row['agent_id'] == 'control':
            raise ConnectorUnavailable('External evidence requires tenant and agent scope')
        return dict(row)

    def _request(self, conn, endpoint, body, key='read-only'):
        url = urlsplit(self.base_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ConnectorUnavailable('External connector requires a configured HTTPS endpoint')
        payload = json.dumps({**body, **self._scope(conn)}, sort_keys=True).encode()
        headers = self._headers(key, payload)
        try:
            response = httpx.post(self.base_url + endpoint, content=payload, headers=headers,
                                  timeout=self.timeout, follow_redirects=False)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError('Invalid response')
            return data
        except Exception:
            # A timeout or HTTP error does not prove an external mutation failed.
            raise ConnectorUnavailable('External provider outcome unavailable; authoritative reconciliation required') from None

    def investigate(self, conn, args, abuse_window_days=30):
        return self._request(conn, '/investigate', {'arguments': args, 'abuse_window_days': abuse_window_days})

    def execute(self, conn, tenant, agent, tool, args, idempotency_key):
        scope = self._scope(conn)
        if scope != {'tenant_id': str(tenant), 'agent_id': str(agent)}:
            raise ConnectorUnavailable('External dispatch scope mismatch')
        result = self._request(conn, '/execute/' + tool,
                               {'tool': tool, 'arguments': args, 'idempotency_key': idempotency_key}, idempotency_key)
        if tool == 'payment.refund' and any(result.get(k) != args.get(k) for k in
                ('customer_id', 'order_id', 'payment_id', 'amount_minor', 'currency')):
            raise ConnectorUnavailable('Refund response does not match the authorized action')
        return result

    def verify(self, conn, tool, result):
        data = self._request(conn, '/verify/' + tool, {'tool': tool, 'result': result})
        if type(data.get('verified')) is not bool:
            raise ConnectorUnavailable('Provider verification is inconclusive')
        return data['verified']

    def reconcile(self, conn, tool, args, idempotency_key):
        data = self._request(conn, '/reconcile/' + tool,
                             {'tool': tool, 'arguments': args, 'idempotency_key': idempotency_key}, idempotency_key)
        if data.get('confirmed') is True and isinstance(data.get('result'), dict):
            result = data['result']
            if tool == 'payment.refund' and any(result.get(k) != args.get(k) for k in
                    ('customer_id', 'order_id', 'payment_id', 'amount_minor', 'currency')):
                raise ConnectorUnavailable('Reconciliation evidence does not match the action')
            return result
        if data.get('status') == 'not_found' and data.get('confirmed') is False:
            return None
        raise ConnectorUnavailable('Provider action remains pending or unknown')
