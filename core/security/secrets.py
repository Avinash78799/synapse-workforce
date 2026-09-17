"""Integration credentials stay in Vault; Postgres stores only scoped references."""
from pathlib import Path
from uuid import UUID
import re
import httpx
from core.config import settings


class VaultSecrets:
    def read(self, reference: str) -> str:
        if not re.fullmatch(r'[a-zA-Z0-9_/-]+', reference) or '..' in reference:
            raise ValueError('Invalid secret reference')
        is_production = getattr(settings, 'app_env', 'development').lower() in ('production', 'prod')
        # Allow direct environment API key fallback only in non-production environments
        if not is_production and settings.llm_api_key and (reference == settings.llm_secret_ref or reference in ('platform/llm', 'llm')):
            return settings.llm_api_key
        try:
            token = Path(settings.vault_token_file).read_text().strip()
            response = httpx.get(f'{settings.vault_addr}/v1/secret/data/{reference}',
                                 headers={'X-Vault-Token': token}, timeout=10)
            if response.status_code != 200:
                raise RuntimeError('Secret unavailable from Vault')
            return response.json()['data']['data']['value']
        except Exception:
            if not is_production and settings.llm_api_key and (reference == settings.llm_secret_ref or reference in ('platform/llm', 'llm')):
                return settings.llm_api_key
            raise RuntimeError('Secret unavailable from Vault')

    def integration_key(self, conn, tenant_id, agent_id, reference):
        # Deliberately uncached. No reusable secret entry survives revocation or
        # a hard-abort, including in other API/worker processes.
        agent=conn.execute('SELECT status,execution_epoch FROM agents WHERE id=%s AND tenant_id=%s',
                           (agent_id,tenant_id)).fetchone()
        if not agent or agent['status']!='active':
            raise PermissionError('Agent is not active')
        reference=str(UUID(str(reference)))
        row=conn.execute('SELECT secret_ref FROM credentials WHERE tenant_id=%s AND agent_id=%s AND secret_ref=%s',
                         (tenant_id,agent_id,reference)).fetchone()
        if not row:
            raise PermissionError('Credential is not authorized for this agent')
        path=f'tenants/{tenant_id}/agents/{agent_id}/integrations/{reference}'
        value=self.read(path)
        fresh=conn.execute('SELECT status,execution_epoch FROM agents WHERE id=%s AND tenant_id=%s',
                           (agent_id,tenant_id)).fetchone()
        if not fresh or fresh['status']!='active' or fresh['execution_epoch']!=agent['execution_epoch']:
            raise PermissionError('Agent context changed during secret lookup')
        return value
