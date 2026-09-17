"""Disposable native tests + full cold-start smoke test. Requires Docker Compose.

Creates isolated project names, a temporary copy, generated test credentials,
and its own volumes; never shuts down the user's normal application stack.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import urllib.request
import http.cookiejar

ROOT=Path(__file__).resolve().parents[1]


def main():
    if not shutil.which('docker'):
        raise SystemExit('Docker is required. No native verification was performed.')
    report_dir=ROOT/'artifacts';report_dir.mkdir(exist_ok=True)
    report={'native_gate_passed':False,'docker_boot_verified':False,'started_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    with tempfile.TemporaryDirectory(prefix='workforce-native-') as temp:
        work=Path(temp)/'project'
        shutil.copytree(ROOT,work,ignore=shutil.ignore_patterns('node_modules','.next','.git','.env','.secrets','__pycache__','.pytest_cache','.venv','artifacts'))
        name='workforce-gate-'+uuid4().hex[:10]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://localhost:{port}'
        env={**os.environ,'WEB_PORT':str(port),'WEB_ORIGIN':base}
        def command(args,timeout=900):
            return subprocess.run(args,cwd=work,env=env,check=True,timeout=timeout,stdout=log,stderr=log)
        def compose(project,file,*args):
            return ['docker','compose','-p',project,'-f',file,*args]
        with (report_dir/'native-gate.log').open('w') as log:
            try:
                command(compose(name+'-tests','docker-compose.test.yml','up','--build','--abort-on-container-exit','--exit-code-from','tests'))
                report['native_tests_passed']=True
                command([sys.executable,'infrastructure/configure_local.py'])
                started=time.monotonic()
                command(compose(name,'docker-compose.yml','up','--build','-d','--wait'),timeout=1200)
                report['cold_start_seconds']=round(time.monotonic()-started,2)
                jar=http.cookiejar.CookieJar()
                http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
                def request(path,body=None,csrf=None):
                    headers={'Content-Type':'application/json'}
                    if csrf:headers['X-CSRF-Token']=csrf
                    req=urllib.request.Request(base+'/api'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
                    with http.open(req,timeout=10) as response:return json.load(response)
                assert request('/health')['status']=='ok'
                config=dict(line.split('=',1) for line in (work/'.env').read_text().splitlines() if '=' in line)
                user=request('/auth/login',{'email':'owner@acme.local','password':config['SEED_PASSWORD']})
                agents=request('/dashboard')['agents']
                support=next(a for a in agents if a['kind']=='support')
                run=request('/runs',{'agent_id':support['id'],'objective':'Resolve duplicate payment','request_key':str(uuid4()),'customer_id':'CUS-002','order_id':'ORD-1002'},user['csrf_token'])
                def wait_status(expected):
                    until=time.monotonic()+60
                    while time.monotonic()<until:
                        detail=request('/runs/'+run['id'])
                        if detail['run']['status']==expected:return detail
                        if detail['run']['status'] in {'failed','aborted','escalated'}:raise RuntimeError('Run did not reach '+expected)
                        time.sleep(.5)
                    raise RuntimeError('Real worker did not reach '+expected)
                wait_status('waiting_approval')
                approval=next(a for a in request('/approvals') if a['run_id']==run['id'])
                finance=request('/auth/login',{'email':'finance_approver@acme.local','password':config['SEED_PASSWORD']})
                request('/approvals/'+approval['id']+'/decision',{'decision':'approved','reason':'Independent native gate verification'},finance['csrf_token'])
                detail=wait_status('completed')
                assert all(t['verification']=='passed' for t in detail['tasks'])
                assert request('/audit/export')['verification']['signed_events']>0
                report.update(docker_boot_verified=True,real_worker_refund_verified=True)
                # Exercise a real Redis outage with durable, undispatched work.
                command(compose(name,'docker-compose.yml','stop','worker'))
                owner=request('/auth/login',{'email':'owner@acme.local','password':config['SEED_PASSWORD']})
                run=request('/runs',{'agent_id':support['id'],'objective':'Resolve queued duplicate payment','request_key':str(uuid4()),'customer_id':'CUS-001','order_id':'ORD-1001'},owner['csrf_token'])
                command(compose(name,'docker-compose.yml','stop','redis'))
                command(compose(name,'docker-compose.yml','up','-d','--wait','redis','worker'))
                detail=wait_status('completed')
                refunds=[row for row in request('/simulation/'+support['id']) if row['kind']=='refund' and row['data']['order_id']=='ORD-1001']
                assert len(refunds)==1
                report.update(native_gate_passed=True,redis_outage_recovery_verified=True)

            except Exception as exc:
                report['failure_type']=type(exc).__name__
                raise
            finally:
                # Only these newly-created, randomly named disposable stacks.
                for project,file in [(name,'docker-compose.yml'),(name+'-tests','docker-compose.test.yml')]:
                    subprocess.run(compose(project,file,'logs','--no-color'),cwd=work,env=env,stdout=log,stderr=log,timeout=60)
                    subprocess.run(compose(project,file,'down','-v','--remove-orphans'),cwd=work,env=env,stdout=log,stderr=log,timeout=120)
                (report_dir/'native-gate.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Native gate passed. Evidence is in artifacts/native-gate.json and native-gate.log.')


if __name__=='__main__':main()
