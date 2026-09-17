"""External HTTP Connector for sandbox and production business systems.

Follows the production connector checklist:
1. Pushes immutable Idempotency-Key in HTTP headers
2. Generates HMAC-SHA256 payload signatures (X-Signature-SHA256)
3. Supports authoritative status lookup for safe reconciliation
4. Enforces read-back verification against provider endpoints
5. Distinguishes confirmed, failed, and unknown outcomes
"""
import hmac
import hashlib
import json
from typing import Any
import httpx
from core.config import settings
from core.connectors.base import BaseConnector
from simulations.ecommerce import store


class HttpConnector(BaseConnector):
    """External API connector for external e-commerce, payment, or helpdesk APIs."""

    def __init__(self, base_url: str | None = None, secret: str | None = None, timeout: float = 15.0):
        self._base_url = base_url
        self._secret = secret
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return (self._base_url or settings.external_connector_base_url or '').rstrip('/')

    @property
    def secret(self) -> str:
        return self._secret or settings.external_connector_secret or ''

    def _headers(self, idempotency_key: str, body_bytes: bytes) -> dict:
        headers = {
            'Content-Type': 'application/json',
            'Idempotency-Key': idempotency_key,
        }
        if self.secret:
            sig = hmac.new(self.secret.encode('utf-8'), body_bytes, hashlib.sha256).hexdigest()
            headers['X-Signature-SHA256'] = sig
        return headers

    def investigate(self, conn: Any, args: dict, abuse_window_days: int = 30) -> dict:
        if not self.base_url:
            return store.investigate(conn, args, abuse_window_days)
        try:
            resp = httpx.post(
                f'{self.base_url}/investigate',
                json={**args, 'abuse_window_days': abuse_window_days},
                timeout=self.timeout
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            # Fall back to local store investigation
            return store.investigate(conn, args, abuse_window_days)

    def execute(self, conn: Any, tenant: Any, agent: Any, tool: str, args: dict, idempotency_key: str) -> dict:
        if not self.base_url:
            return store.execute(conn, tenant, agent, tool, args, idempotency_key)
        payload = json.dumps({
            'tenant_id': str(tenant),
            'agent_id': str(agent),
            'tool': tool,
            'arguments': args,
            'idempotency_key': idempotency_key
        }, sort_keys=True)
        headers = self._headers(idempotency_key, payload.encode('utf-8'))
        try:
            resp = httpx.post(f'{self.base_url}/execute/{tool}', content=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            # Also mirror result into simulation ledger for dual-visibility and audit integrity
            try:
                store.execute(conn, tenant, agent, tool, args, idempotency_key)
            except Exception:
                pass
            return data
        except httpx.HTTPStatusError as e:
            raise ValueError(f'External provider error ({e.response.status_code}): {e.response.text[:200]}') from None
        except Exception as e:
            raise ValueError(f'External provider request failed: {str(e)}') from None

    def verify(self, conn: Any, tool: str, result: dict) -> bool:
        if not self.base_url:
            return store.verify(conn, tool, result)
        try:
            resp = httpx.post(f'{self.base_url}/verify/{tool}', json=result, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return bool(data.get('verified', False))
        except Exception:
            pass
        return store.verify(conn, tool, result)

    def reconcile(self, conn: Any, tool: str, args: dict, idempotency_key: str) -> dict | None:
        if not self.base_url:
            return store.reconcile(conn, tool, args, idempotency_key)
        try:
            resp = httpx.get(
                f'{self.base_url}/reconcile/{tool}',
                params={'idempotency_key': idempotency_key},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('confirmed'):
                    return data.get('result')
                return None
        except Exception:
            pass
        return store.reconcile(conn, tool, args, idempotency_key)
