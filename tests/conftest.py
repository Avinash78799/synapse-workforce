import os
from uuid import UUID
import pytest


@pytest.fixture(autouse=True)
def audit_test_key(tmp_path,monkeypatch):
    path=tmp_path/'audit-key'
    path.write_text('isolated-test-audit-key-never-use-in-deployment-0123456789')
    monkeypatch.setenv('AUDIT_SIGNING_KEY_FILE',str(path))


@pytest.fixture
def database():
    url=os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL is required for PostgreSQL integration tests')
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    if not os.getenv('WORKFORCE_TEST_COMPAT') and not conninfo_to_dict(url)['dbname'].endswith('_test'):
        raise RuntimeError('Integration fixtures may only reset a database whose name ends with _test')
    os.environ['MIGRATION_DATABASE_URL']=url
    from infrastructure.postgres.migrate import migrate
    migrate()
    with psycopg.connect(url) as conn:
        tables=conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('public','auth_private') AND tablename!='schema_migrations'").fetchall()
        conn.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY CASCADE').format(sql.SQL(',').join(sql.Identifier(*t) for t in tables)))
    from simulations.ecommerce.seed import seed, TENANT, OTHER_TENANT, identity
    seed()
    from core.db import scoped
    yield {'tenant':TENANT,'other':OTHER_TENANT,'ops':identity(str(TENANT)+'operations'),
           'support':identity(str(TENANT)+'support'),'other_support':identity(str(OTHER_TENANT)+'support'),
           'owner':identity('owner@acme.local'),'scoped':scoped,'url':url}


@pytest.fixture
def client(database):
    from fastapi.testclient import TestClient
    from apps.api.main import app, redis
    redis.flushdb()  # Only the isolated test Redis DB provided by the harness.
    c=TestClient(app)
    login=c.post('/api/auth/login',json={'email':'owner@acme.local','password':os.environ['SEED_PASSWORD']})
    assert login.status_code==200,login.text
    c.headers['X-CSRF-Token']=login.json()['csrf_token']
    yield c
    c.close()


def pytest_collection_modifyitems(config,items):
    if os.getenv('WORKFORCE_TEST_COMPAT'):
        for item in items:
            if 'native_postgres' in item.keywords:
                item.add_marker(pytest.mark.skip(reason='Requires native login roles and independent PostgreSQL backends'))
