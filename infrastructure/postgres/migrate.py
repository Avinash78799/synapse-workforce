"""Owner-only migrations with checksums and a transaction/advisory lock."""
import hashlib
import os
from pathlib import Path
import psycopg
from psycopg import sql


def migrate():
    with psycopg.connect(os.environ['MIGRATION_DATABASE_URL']) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(198701)')
        for role, env in [('wf_app','APP_DB_PASSWORD'),('wf_auth','AUTH_DB_PASSWORD'),('wf_dispatch','DISPATCH_DB_PASSWORD')]:
            password = os.environ[env]
            if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz DEFAULT now())')
        for path in sorted(Path(__file__).parent.glob('[0-9]*.sql')):
            raw = path.read_text()
            digest = hashlib.sha256(raw.encode()).hexdigest()
            old = conn.execute('SELECT checksum FROM schema_migrations WHERE name=%s',(path.name,)).fetchone()
            if old:
                if old[0] != digest:
                    raise RuntimeError(f'Migration changed after application: {path.name}')
                continue
            conn.execute(raw)
            conn.execute('INSERT INTO schema_migrations(name,checksum) VALUES(%s,%s)',(path.name,digest))
            print(f'Applied {path.name}')


if __name__ == '__main__':
    migrate()
