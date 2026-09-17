"""All business work uses a transaction-local tenant + agent RLS scope."""
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from core.config import settings


@lru_cache
def pool(auth=False):
    return ConnectionPool(settings.auth_database_url if auth else settings.database_url,
                          min_size=1, max_size=12, open=True, kwargs={'row_factory': dict_row})


@contextmanager
def scoped(tenant_id, agent_id='control'):
    tenant_id = str(UUID(str(tenant_id)))
    if agent_id != 'control':
        agent_id = str(UUID(str(agent_id)))
    with pool().connection() as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,)).fetchone()
            conn.execute("SELECT set_config('app.agent_id', %s, true)", (agent_id,)).fetchone()
            yield conn


@contextmanager
def auth_db():
    with pool(True).connection() as conn:
        with conn.transaction():
            yield conn


def assert_runtime_roles():
    """Fail startup if either runtime connection can bypass row-level security."""
    for auth in [False, True]:
        with pool(auth).connection() as conn:
            role = conn.execute('SELECT rolname, rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
            if role['rolname']!=('wf_auth' if auth else 'wf_app') or role['rolsuper'] or role['rolbypassrls'] or role['rolcreaterole']:
                raise RuntimeError('Runtime connection must use its dedicated unprivileged database role')
