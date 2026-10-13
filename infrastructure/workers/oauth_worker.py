"""Dedicated refresh scheduler so long agent runs cannot starve token refresh."""
import logging
import os
import signal
import time
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from core.db import assert_runtime_roles
from core.connectors.oauth_lifecycle import refresh_due, disconnect


def cycle():
    with psycopg.connect(os.environ['DISPATCH_DATABASE_URL'], row_factory=dict_row) as conn:
        role = conn.execute('SELECT current_user AS name').fetchone()
        if role['name'] != 'wf_dispatch':
            raise RuntimeError('OAuth scheduler requires the dispatcher role')
        jobs = conn.execute('SELECT * FROM public.due_vendor_oauth()').fetchall()
    healthy = True
    for job in jobs:
        try:
            from core.db import scoped
            with scoped(job['tenant_id']) as conn:
                state = conn.execute('SELECT status FROM vendor_oauth_connections WHERE installation_id=%s', (job['installation_id'],)).fetchone()
            if state and state['status'] == 'revoking':
                disconnect(job['tenant_id'], job['installation_id'], 'system')
            else:
                refresh_due(job['tenant_id'], job['installation_id'])
        except Exception as error:
            healthy = False
            logging.error('oauth_job_failed installation=%s error_type=%s', job['installation_id'], type(error).__name__)
        else:
            if healthy:
                Path('/tmp/workforce-oauth.heartbeat').touch()
    return healthy


def tick():
    """A completed database scan is required even when no refresh jobs are due."""
    try:
        if cycle():
            Path('/tmp/workforce-oauth.heartbeat').touch()
    except Exception as error:
        logging.error('oauth_cycle_failed error_type=%s', type(error).__name__)


def main():
    assert_runtime_roles()
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        tick()
        time.sleep(5)


if __name__ == '__main__':
    main()
