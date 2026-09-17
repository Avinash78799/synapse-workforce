"""Redis Streams consumer + durable Postgres outbox dispatcher.

Outbox publication is at-least-once; run locks, entity cases, and receipts make
duplicate delivery safe. A dead consumer's pending jobs are reclaimed.
"""
import json
import logging
import multiprocessing
import os
import signal
import socket
import time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from redis import Redis
from redis.exceptions import ResponseError
from core.config import settings
from core.db import assert_runtime_roles
from core.runtime.runs import process_run

log = logging.getLogger('workforce.worker')
STREAM = 'workforce:runs'
GROUP = 'python-workers'


def dispatch(redis):
    with psycopg.connect(os.environ['DISPATCH_DATABASE_URL'],row_factory=dict_row) as conn:
        rows = conn.execute('''SELECT * FROM queue_outbox WHERE published_at IS NULL AND available_at<=now()
                               ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 50''').fetchall()
        for row in rows:
            redis.xadd(STREAM,{k:str(row[k]) for k in ('tenant_id','agent_id','run_id')})
            conn.execute('UPDATE queue_outbox SET published_at=now() WHERE id=%s',(row['id'],))


def freeze_dispatched(job):
    from core.db import scoped
    from core.runtime.audit import audit
    with scoped(job['tenant_id'],job['agent_id']) as conn:
        uncertain=conn.execute("""UPDATE tool_receipts SET state='unknown',updated_at=now()
            WHERE state='dispatched' AND task_id IN (SELECT id FROM agent_tasks WHERE run_id=%s)
            RETURNING task_id""",(job['run_id'],)).fetchall()
        if uncertain:
            audit(conn,job['tenant_id'],job['agent_id'],'worker.interrupted','unknown',run=job['run_id'],
                  details={'actions_frozen':len(uncertain)})


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    assert_runtime_roles()
    with psycopg.connect(os.environ['DISPATCH_DATABASE_URL'],row_factory=dict_row) as conn:
        role=conn.execute('SELECT rolname,rolsuper,rolbypassrls,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
        if role['rolname']!='wf_dispatch' or role['rolsuper'] or role['rolbypassrls'] or role['rolcreaterole']:
            raise RuntimeError('Dispatcher must use its dedicated unprivileged database role')
    redis = Redis.from_url(settings.redis_url,decode_responses=True)
    consumer = socket.gethostname()+'-'+str(uuid4())[:8]
    try:
        redis.xgroup_create(STREAM,GROUP,id='0',mkstream=True)
    except ResponseError as exc:
        if 'BUSYGROUP' not in str(exc):
            raise
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    while running:
        try:
            dispatch(redis)
            Path('/tmp/workforce-worker.heartbeat').touch()
            claimed = redis.xautoclaim(STREAM,GROUP,consumer,min_idle_time=120_000,count=10)
            jobs = claimed[1]
            if not jobs:
                batches = redis.xreadgroup(GROUP,consumer,{STREAM:'>'},count=1,block=1000)
                jobs = batches[0][1] if batches else []
            for message_id,job in jobs:
                try:
                    child = multiprocessing.get_context('spawn').Process(target=process_run,
                        args=(job['tenant_id'],job['agent_id'],job['run_id']))
                    child.start()
                    aborted = False
                    # Durable DB state is authoritative; no missed pubsub message
                    # can leave a hard-aborted worker executing indefinitely.
                    while child.is_alive():
                        child.join(timeout=0.2)
                        Path('/tmp/workforce-worker.heartbeat').touch()
                        from core.db import scoped
                        with scoped(job['tenant_id'],job['agent_id']) as conn:
                            state=conn.execute('SELECT status FROM agents WHERE id=%s',(job['agent_id'],)).fetchone()
                        if state and state['status']=='hard_aborted':
                            child.terminate()
                            child.join(timeout=2)
                            if child.is_alive():
                                child.kill()
                                child.join()
                            freeze_dispatched(job)
                            aborted=True
                            break
                    if child.exitcode!=0 and not aborted:
                        raise RuntimeError('Run subprocess exited unexpectedly')
                    redis.xack(STREAM,GROUP,message_id)
                    redis.xdel(STREAM,message_id)
                except Exception as exc:
                    # Leave pending for recovery; do not expose provider payloads.
                    from core.db import scoped
                    from core.runtime.audit import audit
                    freeze_dispatched(job)
                    attempts = redis.incr('workforce:retries:'+message_id)
                    redis.expire('workforce:retries:'+message_id,86400)
                    log.error(json.dumps({'event':'job_failed','run_id':job.get('run_id'),'error_type':type(exc).__name__,'attempt':attempts}))
                    if attempts>=3:
                        from core.db import scoped
                        from core.runtime.audit import audit
                        with scoped(job['tenant_id'],job['agent_id']) as conn:
                            conn.execute("UPDATE agent_runs SET status='failed',error='Worker retry budget exhausted; reconcile receipts before retrying',finished_at=now() WHERE id=%s AND status NOT IN ('completed','escalated','aborted')",(job['run_id'],))
                            audit(conn,job['tenant_id'],job['agent_id'],'worker.exhausted','failed',run=job['run_id'])
                        redis.xadd('workforce:dead_letters',job)
                        redis.xack(STREAM,GROUP,message_id)
                        redis.xdel(STREAM,message_id)
        except Exception as exc:
            log.error(json.dumps({'event':'worker_cycle_failed','error_type':type(exc).__name__}))
            time.sleep(2)


if __name__=='__main__':
    main()
