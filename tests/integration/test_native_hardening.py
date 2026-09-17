"""Requires native PostgreSQL login roles and independent backend processes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4
import os
import subprocess
import sys
import time
import signal
import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_workflows import start
from tests.integration.test_hardening import login

pytestmark=[pytest.mark.integration,pytest.mark.native_postgres]


def test_native_runtime_roles_and_real_api_lifespan(database):
    from core.db import assert_runtime_roles,scoped
    from apps.api.main import app
    assert_runtime_roles()
    with scoped(database['tenant'],database['support']) as conn:
        assert conn.execute('SELECT current_user AS u').fetchone()['u']=='wf_app'
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==200


@pytest.mark.parametrize('kind',['superuser','bypassrls'])
def test_startup_rejects_privileged_runtime_role(database,monkeypatch,kind):
    import core.db as db
    class BadPool:
        @contextmanager
        def connection(self):
            with psycopg.connect(database['url'],row_factory=dict_row) as conn:
                if kind=='bypassrls':
                    conn.execute('CREATE ROLE wf_test_bypass NOLOGIN NOSUPERUSER BYPASSRLS')
                    conn.execute('SET ROLE wf_test_bypass')
                try:yield conn
                finally:conn.rollback()
    monkeypatch.setattr(db,'pool',lambda auth=False:BadPool())
    with pytest.raises(RuntimeError,match='dedicated unprivileged'):
        db.assert_runtime_roles()


def test_two_approvers_cannot_both_decide_same_request(client,database):
    from apps.api.main import app
    login(client,'operator@acme.local')
    start(client,database,2)
    aid=client.get('/api/approvals').json()[0]['id']
    clients=[TestClient(app),TestClient(app)]
    login(clients[0],'owner@acme.local');login(clients[1],'finance_approver@acme.local')
    barrier=Barrier(2)
    def decide(i):
        barrier.wait()
        return clients[i].post('/api/approvals/'+aid+'/decision',json={'decision':['approved','denied'][i],'reason':'Independent simultaneous decision'}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(decide,[0,1]))==[200,409]
    for c in clients:c.close()
    with database['scoped'](database['tenant'],database['support']) as conn:
        assert conn.execute("SELECT count(*) AS n FROM audit_logs WHERE action='approval.decided'").fetchone()['n']==1


def test_concurrent_operations_triggers_coalesce(client,database):
    from apps.api.main import app
    clients=[TestClient(app) for _ in range(4)]
    for c in clients:login(c,'operator@acme.local')
    barrier=Barrier(4)
    def create(i):
        barrier.wait()
        r=clients[i].post('/api/runs',json={'agent_id':str(database['ops']),'objective':'Run operations','request_key':str(uuid4())})
        assert r.status_code==202
        return r.json()['id']
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert len(set(executor.map(create,range(4))))==1
    for c in clients:c.close()


def test_real_supervisor_freezes_worker_crash_mid_refund(client,database,tmp_path):
    # Process-only injection lives in this test directory, never in app endpoints.
    marker=tmp_path/'refund-started'
    hook=tmp_path/'sitecustomize.py'
    hook.write_text('''import os
from pathlib import Path
from simulations.ecommerce import store
original=store.execute
def interrupted(conn,tenant,agent,tool,args,key):
    if tool=='payment.refund':
        Path(os.environ['CRASH_MARKER']).write_text('dispatched')
        os._exit(17)
    return original(conn,tenant,agent,tool,args,key)
store.execute=interrupted
''')
    r=client.post('/api/runs',json={'agent_id':str(database['support']),'objective':'Investigate duplicate charge','request_key':str(uuid4()),'customer_id':'CUS-001','order_id':'ORD-1001'})
    assert r.status_code==202
    env={**os.environ,'PYTHONPATH':str(tmp_path)+os.pathsep+str(Path.cwd()),'CRASH_MARKER':str(marker)}
    log=(tmp_path/'supervisor.log').open('w')
    worker=subprocess.Popen([sys.executable,'-m','infrastructure.workers.worker'],env=env,stdout=log,stderr=log,start_new_session=True)
    try:
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            frozen=client.get('/api/reconciliation').json()
            if frozen:break
            if worker.poll() is not None:pytest.fail((tmp_path/'supervisor.log').read_text())
            time.sleep(.2)
        else:pytest.fail((tmp_path/'supervisor.log').read_text())
        assert marker.exists() and frozen[0]['tool']=='payment.refund'
        with database['scoped'](database['tenant'],database['support']) as conn:
            assert conn.execute("SELECT count(*) AS n FROM sim_records WHERE kind='refund'").fetchone()['n']==0
        assert client.post('/api/reconciliation/'+frozen[0]['id']+'/resolve',json={'choice':'verify','reason':'Check whether provider applied refund'}).status_code==409
    finally:
        os.killpg(worker.pid,signal.SIGTERM)
        try:worker.wait(timeout=5)
        except subprocess.TimeoutExpired:os.killpg(worker.pid,signal.SIGKILL);worker.wait()
        log.close()


def test_lost_redis_publish_ack_is_recovered_without_duplicate_refund(client,database):
    from redis import Redis
    from infrastructure.workers.worker import dispatch,STREAM
    from core.runtime.runs import process_run
    redis=Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
    r=client.post('/api/runs',json={'agent_id':str(database['support']),'objective':'Resolve duplicate','request_key':str(uuid4()),'customer_id':'CUS-001','order_id':'ORD-1001'})
    assert r.status_code==202
    class LostAck:
        def xadd(self,*args,**kwargs):
            redis.xadd(*args,**kwargs)
            raise ConnectionError('Injected lost Redis acknowledgement')
    with pytest.raises(ConnectionError):dispatch(LostAck())
    with database['scoped'](database['tenant'],database['support']) as conn:
        assert conn.execute('SELECT published_at FROM queue_outbox WHERE run_id=%s',(r.json()['id'],)).fetchone()['published_at'] is None
    dispatch(redis)
    jobs=redis.xrange(STREAM)
    assert len(jobs)==2
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda item:process_run(item[1]['tenant_id'],item[1]['agent_id'],item[1]['run_id']),jobs))
    with database['scoped'](database['tenant'],database['support']) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sim_records WHERE kind='refund'").fetchone()['n']==1


def test_actual_hard_abort_terminates_slow_refund_process(client,database,tmp_path):
    marker=tmp_path/'slow-refund'
    (tmp_path/'sitecustomize.py').write_text('''import os,time
from pathlib import Path
from simulations.ecommerce import store
original=store.execute
def slow(conn,tenant,agent,tool,args,key):
    if tool=='payment.refund':
        Path(os.environ['CRASH_MARKER']).write_text('started')
        time.sleep(20)
    return original(conn,tenant,agent,tool,args,key)
store.execute=slow
''')
    r=client.post('/api/runs',json={'agent_id':str(database['support']),'objective':'Resolve duplicate','request_key':str(uuid4()),'customer_id':'CUS-001','order_id':'ORD-1001'})
    env={**os.environ,'PYTHONPATH':str(tmp_path)+os.pathsep+str(Path.cwd()),'CRASH_MARKER':str(marker)}
    log=(tmp_path/'abort.log').open('w')
    worker=subprocess.Popen([sys.executable,'-m','infrastructure.workers.worker'],env=env,stdout=log,stderr=log,start_new_session=True)
    try:
        deadline=time.monotonic()+25
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.1)
        assert marker.exists(),(tmp_path/'abort.log').read_text()
        # Lifecycle must not wait for the provider's receipt lock; it only sets
        # the durable epoch and supervisor kills the child before cleanup.
        result=client.post('/api/agents/'+str(database['support'])+'/lifecycle',json={'state':'hard_aborted','reason':'Stop slow refund execution'})
        assert result.status_code==200
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if client.get('/api/reconciliation').json():break
            time.sleep(.1)
        assert client.get('/api/reconciliation').json()
        with database['scoped'](database['tenant'],database['support']) as conn:
            assert conn.execute("SELECT count(*) AS n FROM sim_records WHERE kind='refund'").fetchone()['n']==0
        assert client.get('/api/runs/'+r.json()['id']).json()['run']['status']=='aborted'
    finally:
        os.killpg(worker.pid,signal.SIGTERM)
        try:worker.wait(timeout=5)
        except subprocess.TimeoutExpired:os.killpg(worker.pid,signal.SIGKILL);worker.wait()
        log.close()
