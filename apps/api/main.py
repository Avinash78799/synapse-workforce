from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hmac
import logging
import json
from time import monotonic
import secrets
from typing import Literal
from uuid import UUID, uuid4
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.types.json import Jsonb
from redis import Redis
from core.config import settings
from core.db import scoped, auth_db, assert_runtime_roles
from core.security.passwords import verify_password, hash_password, token_hash
from core.runtime.audit import audit, signing_key, export_audit
from core.runtime.runs import create_run, enqueue
from core.policies.engine import PolicyRules
from core.memory.knowledge import ingest, retrieve
from core.models.prompts import MISSING_KNOWLEDGE
from core.security.payment_data import redact_payment_data

log = logging.getLogger('workforce.api')
redis = Redis.from_url(settings.redis_url,decode_responses=True)
DUMMY_HASH = hash_password('not-a-real-user-password')


@asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    assert_runtime_roles()
    signing_key()
    redis.ping()
    yield


app = FastAPI(title='AI Workforce API',version='1.1.0',lifespan=lifespan)


@app.middleware('http')
async def request_boundary(request: Request, call_next):
    started=monotonic()
    request_id = str(uuid4())
    if request.method not in {'GET','HEAD','OPTIONS'}:
        origin = request.headers.get('origin')
        if origin and origin!=settings.web_origin:
            return JSONResponse({'detail':'Origin is not allowed'},status_code=403)
        if int(request.headers.get('content-length','0'))>100_000:
            return JSONResponse({'detail':'Request body exceeds 100 KB'},status_code=413)
    response = await call_next(request)
    response.headers['X-Request-ID']=request_id
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    log.info(json.dumps({'event':'http_request','request_id':request_id,'method':request.method,
                         'path':request.url.path,'status':response.status_code,
                         'elapsed_ms':round((monotonic()-started)*1000,2)}))
    return response


@app.exception_handler(Exception)
async def unexpected(request, exc):
    log.error('request_failed type=%s path=%s',type(exc).__name__,request.url.path)
    return JSONResponse({'detail':'The request could not be completed. Check service health and try again.'},status_code=500)


def rate_limit(key, limit, seconds):
    # Atomic INCR+EXPIRE; a process crash cannot leave an immortal counter.
    count = redis.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",1,key,seconds)
    if count>limit:
        raise HTTPException(429,'Rate limit reached; try again later')


def current_user(request: Request):
    token = request.cookies.get('wf_session')
    if not token:
        raise HTTPException(401,'Sign in to continue')
    with auth_db() as conn:
        user = conn.execute('''SELECT u.id,u.tenant_id,u.email,u.name,u.role,s.csrf_token
          FROM auth_private.sessions s JOIN auth_private.users u ON u.id=s.user_id AND u.tenant_id=s.tenant_id
          WHERE s.token_hash=%s AND s.expires_at>now() AND u.active''',(token_hash(token),)).fetchone()
    if not user:
        raise HTTPException(401,'Session expired; sign in again')
    if request.method not in {'GET','HEAD','OPTIONS'} and not hmac.compare_digest(user['csrf_token'],request.headers.get('x-csrf-token','')):
        raise HTTPException(403,'CSRF token is missing or invalid')
    return user


def require(user,*roles):
    if user['role'] not in roles:
        raise HTTPException(403,'Your role cannot perform this action')


def find_agent(conn, agent_id, lock=False):
    agent = conn.execute('SELECT * FROM agents WHERE id=%s'+(' FOR UPDATE' if lock else ''),(agent_id,)).fetchone()
    if not agent:
        raise HTTPException(404,'Agent not found')
    return agent


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Body):
    email: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=1,max_length=256)


@app.post('/api/auth/login')
def login(body: Login,request: Request,response: Response):
    rate_limit('auth:'+token_hash((request.client.host if request.client else 'unknown')+body.email.lower()),8,300)
    with auth_db() as conn:
        user = conn.execute('SELECT * FROM auth_private.users WHERE lower(email)=%s AND active',(body.email.strip().lower(),)).fetchone()
        valid = verify_password(body.password,user['password_hash'] if user else DUMMY_HASH)
        if not user or not valid:
            raise HTTPException(401,'Email or password is incorrect')
        old_token=request.cookies.get('wf_session')
        if old_token:
            conn.execute('DELETE FROM auth_private.sessions WHERE token_hash=%s',(token_hash(old_token),))
        token,csrf = secrets.token_urlsafe(48),secrets.token_urlsafe(32)
        conn.execute('DELETE FROM auth_private.sessions WHERE expires_at<now()')
        conn.execute('INSERT INTO auth_private.sessions(token_hash,user_id,tenant_id,csrf_token,expires_at) VALUES(%s,%s,%s,%s,%s)',
                     (token_hash(token),user['id'],user['tenant_id'],csrf,datetime.now(timezone.utc)+timedelta(hours=8)))
    response.set_cookie('wf_session',token,httponly=True,secure=settings.secure_cookies,samesite='strict',max_age=28800,path='/')
    return {'id':user['id'],'name':user['name'],'email':user['email'],'role':user['role'],'tenant_id':user['tenant_id'],'csrf_token':csrf}


@app.get('/api/auth/me')
def me(user=Depends(current_user)):
    return user


@app.post('/api/auth/logout')
def logout(request: Request,response: Response,user=Depends(current_user)):
    with auth_db() as conn:
        conn.execute('DELETE FROM auth_private.sessions WHERE token_hash=%s',(token_hash(request.cookies['wf_session']),))
    response.delete_cookie('wf_session',path='/')
    return {'ok':True}


@app.get('/api/health')
def health():
    try:
        with auth_db() as conn:
            conn.execute('SELECT 1')
        redis.ping()
    except Exception:
        raise HTTPException(503,'Database or queue is unavailable')
    return {'status':'ok','environment':'simulation','model_provider':settings.model_provider}


@app.get('/api/dashboard')
def dashboard(user=Depends(current_user)):
    with scoped(user['tenant_id']) as conn:
        tenant = conn.execute('SELECT * FROM tenants').fetchone()
        agents = conn.execute('SELECT * FROM agents ORDER BY kind').fetchall()
        runs = conn.execute('SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT 30').fetchall()
        metrics = conn.execute('''SELECT count(*)::int AS total_runs,
          count(*) FILTER(WHERE status='completed')::int AS completed_runs,
          count(*) FILTER(WHERE status='escalated')::int AS escalated_runs,
          COALESCE(sum((summary->>'actions_verified')::int),0)::int AS verified_actions,
          sum(cost_usd) AS model_cost_usd,
          count(*) FILTER(WHERE cost_usd IS NULL)::int AS unpriced_runs
          FROM agent_runs''').fetchone()
        approvals = conn.execute("SELECT count(*)::int AS count FROM approval_requests WHERE status='pending' AND expires_at>now()").fetchone()['count']
        action_counts=conn.execute('''SELECT count(*)::int AS planned,
          count(*) FILTER(WHERE status='completed' AND approval_id IS NULL)::int AS automatic
          FROM agent_tasks WHERE position>0''').fetchone()
        support_counts=conn.execute('''SELECT count(*)::int AS total,
          count(*) FILTER(WHERE r.status='completed')::int AS resolved
          FROM agent_runs r JOIN agents a ON a.id=r.agent_id WHERE a.kind='support' ''').fetchone()
        metrics['automation_rate']=round(action_counts['automatic']/action_counts['planned']*100,1) if action_counts['planned'] else None
        metrics['support_resolution_rate']=round(support_counts['resolved']/support_counts['total']*100,1) if support_counts['total'] else None
        activity = conn.execute('SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 8').fetchall()
    return {'tenant':tenant,'agents':agents,'runs':runs,'metrics':{**metrics,'pending_approvals':approvals},
            'activity':activity,'environment':'simulation','model_provider':settings.model_provider}


@app.get('/api/agents/{agent_id}')
def agent_detail(agent_id: UUID,user=Depends(current_user)):
    with scoped(user['tenant_id'],agent_id) as conn:
        agent = find_agent(conn,agent_id)
        data = {'agent':agent}
        for name,table in [('skills','agent_skills'),('tools','agent_tools'),('permissions','agent_permissions'),('integrations','integrations')]:
            data[name] = conn.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
        data['policy'] = conn.execute('SELECT * FROM agent_policies WHERE version=%s',(agent['policy_version'],)).fetchone()
        data['memory'] = conn.execute('SELECT * FROM memory_items ORDER BY created_at DESC LIMIT 12').fetchall()
        data['knowledge'] = conn.execute('SELECT id,title,trust,created_at FROM knowledge_documents ORDER BY created_at DESC').fetchall()
    return data


class RunInput(Body):
    agent_id: UUID
    objective: str = Field(min_length=3,max_length=4000)
    request_key: UUID
    customer_id: str | None = Field(default=None,pattern=r'^CUS-[0-9]+$')
    order_id: str | None = Field(default=None,pattern=r'^ORD-[0-9]+$')

    _redact=field_validator('objective')(redact_payment_data)


@app.post('/api/runs',status_code=202)
def start_run(body: RunInput,user=Depends(current_user)):
    require(user,'owner','operator')
    rate_limit('runs:'+str(user['tenant_id'])+':'+str(body.agent_id),settings.max_runs_per_hour,3600)
    with scoped(user['tenant_id'],body.agent_id) as conn:
        agent = find_agent(conn,body.agent_id,lock=True)
        inputs = {}
        if agent['kind']=='support':
            if not body.customer_id or not body.order_id:
                raise HTTPException(422,'Select a customer and order for the support investigation')
            from simulations.ecommerce.store import investigate
            inputs={'customer_id':body.customer_id,'order_id':body.order_id}
            try:
                investigate(conn,inputs)
            except ValueError as exc:
                raise HTTPException(422,str(exc))
        try:
            return create_run(conn,user,agent,body.objective,body.request_key,inputs)
        except ValueError as exc:
            raise HTTPException(409,str(exc))


@app.get('/api/runs/{run_id}')
def run_detail(run_id: UUID,user=Depends(current_user)):
    with scoped(user['tenant_id']) as conn:
        run = conn.execute('SELECT * FROM agent_runs WHERE id=%s',(run_id,)).fetchone()
        if not run:
            raise HTTPException(404,'Run not found')
        tasks = conn.execute('SELECT * FROM agent_tasks WHERE run_id=%s ORDER BY position',(run_id,)).fetchall()
        audit_rows = conn.execute('SELECT * FROM audit_logs WHERE run_id=%s ORDER BY created_at,id',(run_id,)).fetchall()
    return {'run':run,'tasks':tasks,'audit':audit_rows}


@app.get('/api/approvals')
def approvals(user=Depends(current_user)):
    with scoped(user['tenant_id']) as conn:
        return conn.execute('''SELECT a.*, t.tool, t.arguments, g.name AS agent_name, r.actor_id AS requester_id
          FROM approval_requests a JOIN agent_tasks t ON t.id=a.task_id JOIN agents g ON g.id=a.agent_id
          JOIN agent_runs r ON r.id=a.run_id
          ORDER BY a.created_at DESC LIMIT 100''').fetchall()


class ApprovalDecision(Body):
    decision: Literal['approved','denied']
    reason: str = Field(min_length=5,max_length=500)
    _redact=field_validator('reason')(redact_payment_data)


@app.post('/api/approvals/{approval_id}/decision')
def decide(approval_id: UUID,body: ApprovalDecision,user=Depends(current_user)):
    from core.security.approvals import guard, rejected
    guard(redis,user)
    failure=None
    with scoped(user['tenant_id']) as conn:
        approval = conn.execute('SELECT * FROM approval_requests WHERE id=%s FOR UPDATE',(approval_id,)).fetchone()
        if not approval:
            failure=(404,'Approval not found','unknown_approval')
        elif user['role'] not in {'owner',approval['required_role']}:
            failure=(403,'Your role cannot decide this approval','role_escalation_attempt')
        elif approval['status']!='pending':
            failure=(409,'This request already has a decision',None)
        else:
            run=conn.execute('SELECT actor_id FROM agent_runs WHERE id=%s',(approval['run_id'],)).fetchone()
            if approval['independent_required'] and run['actor_id']==user['id']:
                failure=(403,'An independent approver must decide this financial action','self_approval_attempt')
            else:
                expired=approval['expires_at']<=datetime.now(timezone.utc)
                decision='expired' if expired else body.decision
                conn.execute('UPDATE approval_requests SET status=%s,decided_by=%s,decision_reason=%s,decided_at=now() WHERE id=%s',
                    (decision,user['id'],'Default deny on timeout' if expired else body.reason,approval_id))
                audit(conn,user['tenant_id'],approval['agent_id'],'approval.decided',decision,actor=user['id'],run=approval['run_id'],
                    task=approval['task_id'],policy=approval['policy_version'],approval=approval_id,details={'reason':body.reason})
                enqueue(conn,user['tenant_id'],approval['agent_id'],approval['run_id'])
    if failure:
        if failure[2]:
            rejected(redis,user,failure[2],approval_id)
        raise HTTPException(failure[0],failure[1])
    return {'status':decision}


class Lifecycle(Body):
    state: Literal['active','draining','hard_aborted','inactive']
    reason: str = Field(min_length=5,max_length=500)
    password: str | None = Field(default=None,max_length=256)
    _redact=field_validator('reason')(redact_payment_data)


@app.post('/api/agents/{agent_id}/lifecycle')
def lifecycle(agent_id: UUID,body: Lifecycle,user=Depends(current_user)):
    require(user,'owner')
    password_valid=False
    if body.password:
        rate_limit('unlock:'+str(user['id']),5,300)
        with auth_db() as auth:
            stored = auth.execute('SELECT password_hash FROM auth_private.users WHERE id=%s',(user['id'],)).fetchone()
        password_valid=bool(stored and verify_password(body.password,stored['password_hash']))
    with scoped(user['tenant_id'],agent_id) as conn:
        agent = find_agent(conn,agent_id,lock=True)
        if agent['status']=='hard_aborted' and body.state!='hard_aborted':
            if not password_valid:
                raise HTTPException(403,'Owner password is required to unlock a hard-aborted agent')
        conn.execute('UPDATE agents SET status=%s,kill_reason=%s,execution_epoch=execution_epoch+%s WHERE id=%s',
                     (body.state,body.reason,1 if body.state=='hard_aborted' else 0,agent_id))
        if body.state=='hard_aborted':
            conn.execute("UPDATE agent_runs SET status='aborted',error='Emergency stop; context invalidated and reconciliation required' WHERE status IN ('running','queued','waiting_approval','paused')")
        if body.state=='active':
            for run in conn.execute("SELECT id FROM agent_runs WHERE status='paused'").fetchall():
                enqueue(conn,user['tenant_id'],agent_id,run['id'])
        audit(conn,user['tenant_id'],agent_id,'agent.lifecycle',body.state,actor=user['id'],policy=agent['policy_version'],details={'reason':body.reason})
    if body.state=='hard_aborted':
        # Commit the abort epoch first: the supervisor must be able to observe
        # it while a provider holds a receipt lock. Never wait for that lock in
        # the transaction that publishes the emergency stop.
        with scoped(user['tenant_id'],agent_id) as conn:
            conn.execute("""UPDATE tool_receipts SET state='unknown',updated_at=now()
                WHERE state='dispatched' AND idempotency_key IN (
                    SELECT idempotency_key FROM tool_receipts WHERE state='dispatched'
                    FOR UPDATE SKIP LOCKED)""")
    return {'status':body.state}


class Toggle(Body):
    enabled: bool


@app.put('/api/agents/{agent_id}/skills/{name}')
def skill_toggle(agent_id: UUID,name: str,body: Toggle,user=Depends(current_user)):
    require(user,'owner')
    with scoped(user['tenant_id'],agent_id) as conn:
        agent=find_agent(conn,agent_id)
        updated = conn.execute('UPDATE agent_skills SET enabled=%s WHERE name=%s RETURNING name',(body.enabled,name)).fetchone()
        if not updated:
            raise HTTPException(404,'Skill not found')
        audit(conn,user['tenant_id'],agent_id,'skill.updated','enabled' if body.enabled else 'disabled',actor=user['id'],policy=agent['policy_version'],details={'skill':name})
    return {'ok':True}


@app.put('/api/agents/{agent_id}/tools/{name}')
def tool_toggle(agent_id: UUID,name: str,body: Toggle,user=Depends(current_user)):
    require(user,'owner')
    with scoped(user['tenant_id'],agent_id) as conn:
        agent=find_agent(conn,agent_id)
        updated = conn.execute('UPDATE agent_tools SET enabled=%s WHERE name=%s RETURNING name',(body.enabled,name)).fetchone()
        if not updated:
            raise HTTPException(404,'Tool not found')
        audit(conn,user['tenant_id'],agent_id,'tool.permission.updated','enabled' if body.enabled else 'disabled',actor=user['id'],policy=agent['policy_version'],tool=name)
    return {'ok':True}


@app.post('/api/agents/{agent_id}/policies')
def update_policy(agent_id: UUID,body: PolicyRules,user=Depends(current_user)):
    require(user,'owner')
    if body.currency!='INR' or body.approval_role!='finance_approver':
        raise HTTPException(422,'V1 supports INR and the finance_approver role')
    with scoped(user['tenant_id'],agent_id) as conn:
        agent=find_agent(conn,agent_id,lock=True)
        version=agent['policy_version']+1
        conn.execute('INSERT INTO agent_policies(tenant_id,agent_id,version,rules,created_by) VALUES(%s,%s,%s,%s,%s)',
                     (user['tenant_id'],agent_id,version,Jsonb(body.model_dump()),str(user['id'])))
        conn.execute('UPDATE agents SET policy_version=%s WHERE id=%s',(version,agent_id))
        audit(conn,user['tenant_id'],agent_id,'policy.version.created','completed',actor=user['id'],policy=version)
    return {'version':version}


class Autonomy(Body):
    level: int = Field(ge=0,le=5)
    reason: str = Field(min_length=5,max_length=500)
    _redact=field_validator('reason')(redact_payment_data)


@app.put('/api/agents/{agent_id}/autonomy')
def autonomy(agent_id: UUID,body: Autonomy,user=Depends(current_user)):
    require(user,'owner')
    with scoped(user['tenant_id'],agent_id) as conn:
        agent=find_agent(conn,agent_id,lock=True)
        if body.level>agent['autonomy'] and body.level>=4:
            # Concrete conservative promotion gate. V1 seeded level 4 is
            # simulation-only; no automatic production promotion is implied.
            evidence = conn.execute("SELECT status FROM agent_runs ORDER BY created_at DESC LIMIT 100").fetchall()
            if len(evidence)<100 or sum(r['status']=='completed' for r in evidence)<95:
                raise HTTPException(409,'Promotion requires 100 measured runs with at least 95 completed successfully, followed by owner sign-off')
            incidents=conn.execute("SELECT count(*) AS n FROM audit_logs WHERE result='unauthorized'").fetchone()['n']
            if incidents:
                raise HTTPException(409,'Unauthorized-action incidents block promotion')
        conn.execute('UPDATE agents SET autonomy=%s WHERE id=%s',(body.level,agent_id))
        audit(conn,user['tenant_id'],agent_id,'autonomy.changed','completed',actor=user['id'],policy=agent['policy_version'],details={'from':agent['autonomy'],'to':body.level,'sign_off':body.reason})
    return {'level':body.level}


@app.get('/api/simulation/{agent_id}')
def simulation(agent_id: UUID,user=Depends(current_user)):
    with scoped(user['tenant_id'],agent_id) as conn:
        find_agent(conn,agent_id)
        return conn.execute('SELECT kind,id,data FROM sim_records ORDER BY kind,id').fetchall()


@app.get('/api/audit')
def audit_list(user=Depends(current_user)):
    with scoped(user['tenant_id']) as conn:
        return conn.execute('SELECT * FROM audit_logs ORDER BY created_at DESC,id DESC LIMIT 200').fetchall()


class Knowledge(Body):
    title: str = Field(min_length=3,max_length=150)
    content: str = Field(min_length=20,max_length=20000)
    _redact=field_validator('title','content')(redact_payment_data)


@app.post('/api/agents/{agent_id}/knowledge')
def add_knowledge(agent_id: UUID,body: Knowledge,user=Depends(current_user)):
    require(user,'owner','operator')
    with scoped(user['tenant_id'],agent_id) as conn:
        agent=find_agent(conn,agent_id)
        doc=ingest(conn,user['tenant_id'],agent_id,body.title,body.content)
        audit(conn,user['tenant_id'],agent_id,'knowledge.ingested','completed',actor=user['id'],policy=agent['policy_version'],details={'document_id':str(doc)})
    return {'id':doc}


class Query(Body):
    query: str = Field(min_length=3,max_length=1000)
    _redact=field_validator('query')(redact_payment_data)


@app.post('/api/agents/{agent_id}/knowledge/query')
def query_knowledge(agent_id: UUID,body: Query,user=Depends(current_user)):
    with scoped(user['tenant_id'],agent_id) as conn:
        find_agent(conn,agent_id)
        rows=retrieve(conn,user['tenant_id'],agent_id,body.query)
        evidence=[]
        for row in rows:
            if row['similarity']>=0.12:
                doc=conn.execute('SELECT title FROM knowledge_documents WHERE id=%s',(row['document_id'],)).fetchone()
                evidence.append({'text':row['content'],'citation':'[Doc: '+doc['title']+']','document_id':str(row['document_id'])})
    # Extractive, cited evidence is deliberately separate from execution and
    # cannot issue instructions. No free-form uncited factual answer is generated.
    return {'answer':None if evidence else MISSING_KNOWLEDGE,'sources':evidence,'mode':'extractive_simulation_retrieval'}


@app.get('/api/agents/{agent_id}/evaluations')
def evaluation_history(agent_id: UUID,user=Depends(current_user)):
    with scoped(user['tenant_id'],agent_id) as conn:
        find_agent(conn,agent_id)
        return conn.execute('SELECT * FROM evaluation_runs ORDER BY created_at DESC LIMIT 20').fetchall()


@app.post('/api/agents/{agent_id}/evaluations')
def evaluate_agent(agent_id: UUID,user=Depends(current_user)):
    require(user,'owner','operator')
    from core.evaluation.suite import run_suite
    result=run_suite()
    with scoped(user['tenant_id'],agent_id) as conn:
        agent=find_agent(conn,agent_id)
        conn.execute('INSERT INTO evaluation_runs(id,tenant_id,agent_id,suite_version,passed,total,results) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                     (uuid4(),user['tenant_id'],agent_id,result['suite_version'],result['passed'],result['total'],Jsonb(result['results'])))
        audit(conn,user['tenant_id'],agent_id,'evaluation.completed','passed' if result['passed']==result['total'] else 'failed',actor=user['id'],policy=agent['policy_version'],details={'suite':result['suite_version'],'passed':result['passed'],'total':result['total']})
    return result


class SkillAutonomy(Body):
    cap: int | None = Field(default=None,ge=0,le=5)


@app.put('/api/agents/{agent_id}/skills/{name}/autonomy')
def skill_autonomy(agent_id: UUID,name: str,body: SkillAutonomy,user=Depends(current_user)):
    require(user,'owner')
    with scoped(user['tenant_id'],agent_id) as conn:
        agent=find_agent(conn,agent_id,lock=True)
        old=conn.execute('SELECT autonomy_cap FROM agent_skills WHERE name=%s FOR UPDATE',(name,)).fetchone()
        if not old:
            raise HTTPException(404,'Skill not found')
        if body.cap is not None and body.cap>agent['autonomy']:
            raise HTTPException(422,'A skill cap cannot exceed agent autonomy')
        conn.execute('UPDATE agent_skills SET autonomy_cap=%s WHERE name=%s',(body.cap,name))
        audit(conn,user['tenant_id'],agent_id,'skill.autonomy.changed','completed',actor=user['id'],
              policy=agent['policy_version'],details={'skill':name,'from':old['autonomy_cap'],'to':body.cap})
    return {'cap':body.cap}


@app.get('/api/reconciliation')
def reconciliation_list(user=Depends(current_user)):
    with scoped(user['tenant_id']) as conn:
        return conn.execute("""SELECT t.id,t.run_id,t.agent_id,t.tool,t.arguments,t.reason,t.verification,
          r.state,r.result,r.updated_at,r.idempotency_key,g.name AS agent_name
          FROM tool_receipts r JOIN agent_tasks t ON t.id=r.task_id JOIN agents g ON g.id=t.agent_id
          WHERE r.state='unknown' ORDER BY r.updated_at LIMIT 200""").fetchall()


class ReconciliationDecision(Body):
    choice: Literal['verify','close_unresolved']
    reason: str = Field(min_length=10,max_length=500)
    _redact=field_validator('reason')(redact_payment_data)


@app.post('/api/reconciliation/{task_id}/resolve')
def reconciliation_decide(task_id: UUID,body: ReconciliationDecision,user=Depends(current_user)):
    require(user,'owner','finance_approver')
    rate_limit('reconciliation:'+str(user['id']),10,60)
    from core.runtime.reconciliation import resolve
    with scoped(user['tenant_id']) as conn:
        return resolve(conn,user,task_id,body.choice,body.reason)


@app.get('/api/audit/export')
def audit_export(user=Depends(current_user)):
    require(user,'owner','finance_approver')
    rate_limit('audit-export:'+str(user['id']),5,60)
    with scoped(user['tenant_id']) as conn:
        # A single SELECT provides a consistent statement snapshot of all chains.
        try:
            document=export_audit(conn,user['tenant_id'])
        except ValueError:
            raise HTTPException(409,'Audit integrity verification failed; preserve evidence and investigate')
    return JSONResponse(document,headers={'Content-Disposition':'attachment; filename="workforce-audit.json"'})


@app.get('/api/security/signals')
def security_signals(user=Depends(current_user)):
    require(user,'owner','finance_approver')
    with scoped(user['tenant_id']) as conn:
        return conn.execute("SELECT id,actor,details,created_at FROM audit_logs WHERE action='approval.security' ORDER BY id DESC LIMIT 50").fetchall()


@app.get('/api/budgets')
def budgets(user=Depends(current_user)):
    day=datetime.now(timezone.utc).date()
    reserved=int(redis.get(f"workforce:model-budget:{user['tenant_id']}:{day}") or 0)/1_000_000
    with scoped(user['tenant_id']) as conn:
        totals=conn.execute("""SELECT COALESCE(sum(cost_usd),0) AS recorded_cost_usd,
          count(*) FILTER(WHERE cost_usd IS NULL AND model_attempts>0)::int AS uncertain_runs,
          COALESCE(sum(input_tokens+output_tokens),0)::int AS tokens
          FROM agent_runs WHERE created_at >= %s::date AT TIME ZONE 'UTC'
          AND created_at < (%s::date+1) AT TIME ZONE 'UTC'""",(day,day)).fetchone()
        runs=conn.execute('SELECT id,objective,provider,cost_usd,input_tokens,output_tokens,created_at FROM agent_runs ORDER BY created_at DESC LIMIT 50').fetchall()
    return {'day_utc':str(day),'provider':settings.model_provider,'daily_limit_usd':settings.daily_model_budget_usd,
            'reserved_usd':reserved,'remaining_usd':max(0,settings.daily_model_budget_usd-reserved),
            **totals,'runs':runs,'basis':'Recorded usage for runs created today; conservative reservations include uncertain requests. Not a billing statement.'}


# ==============================================================================
# DPDP Act, 2023 (Digital Personal Data Protection Act) Compliance APIs
# ==============================================================================

class ConsentInput(Body):
    data_principal_id: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=100)
    notice_version: str = '1.0'
    metadata: dict = Field(default_factory=dict)


class ConsentWithdrawInput(Body):
    data_principal_id: str = Field(min_length=1, max_length=100)
    purpose: str | None = None


class PrincipalInput(Body):
    data_principal_id: str = Field(min_length=1, max_length=100)


class GrievanceInput(Body):
    data_principal_id: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=2000)


class BreachInput(Body):
    incident_title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=2000)
    severity: Literal['low', 'medium', 'high', 'critical'] = 'medium'
    affected_principals_count: int = Field(default=0, ge=0)
    categories_of_data: list[str] = Field(default_factory=list)


@app.get('/api/privacy/notice')
def privacy_notice():
    """Statutory notice under Section 5 of the Digital Personal Data Protection Act, 2023."""
    return {
        'statutory_act': 'Digital Personal Data Protection Act, 2023 (India)',
        'section_compliance': 'Section 5 (Notice) and Section 6 (Consent)',
        'data_fiduciary': 'AI Agent Workforce Platform',
        'specified_purposes': [
            {'purpose': 'support.order_resolution', 'description': 'Investigate duplicate transactions, manage tickets, and issue authorized customer refunds'},
            {'purpose': 'operations.fulfillment', 'description': 'Track delayed shipments, retry failed payments, and manage inventory operations'},
            {'purpose': 'security.audit', 'description': 'Cryptographic fraud prevention, identity verification, and anti-abuse protection'}
        ],
        'data_principal_rights': [
            {'right': 'Right to Access (Section 11)', 'mechanism': 'Submit DSAR request via /api/privacy/dsar'},
            {'right': 'Right to Correction & Erasure (Section 12)', 'mechanism': 'Request data erasure via /api/privacy/erase'},
            {'right': 'Right of Grievance Redressal (Section 13)', 'mechanism': 'Submit grievance via /api/privacy/grievance'},
            {'right': 'Right to Nominate (Section 14)', 'mechanism': 'Register nominee contact with DPO'}
        ],
        'data_protection_officer': {
            'name': settings.dpo_name,
            'email': settings.dpo_email,
            'phone': settings.dpo_phone,
            'notice_url': settings.privacy_notice_url
        },
        'appeal_body': 'Data Protection Board of India (DPBI) - https://meity.gov.in'
    }


@app.post('/api/privacy/consent')
def consent_grant(body: ConsentInput, user=Depends(current_user)):
    """Records affirmative, purpose-specific consent from a Data Principal."""
    from core.privacy.consent import record_consent
    with scoped(user['tenant_id']) as conn:
        record = record_consent(conn, user['tenant_id'], body.data_principal_id, body.purpose, body.notice_version, body.metadata)
        audit(conn, user['tenant_id'], None, 'privacy.consent_granted', 'granted', actor=user['id'],
              details={'data_principal_id': body.data_principal_id, 'purpose': body.purpose})
    return {'status': 'recorded', 'consent': record}


@app.post('/api/privacy/consent/withdraw')
def consent_withdraw(body: ConsentWithdrawInput, user=Depends(current_user)):
    """Withdraws consent per Section 6(4) of the DPDP Act."""
    from core.privacy.consent import withdraw_consent
    with scoped(user['tenant_id']) as conn:
        count = withdraw_consent(conn, user['tenant_id'], body.data_principal_id, body.purpose)
        audit(conn, user['tenant_id'], None, 'privacy.consent_withdrawn', 'withdrawn', actor=user['id'],
              details={'data_principal_id': body.data_principal_id, 'purpose': body.purpose, 'records_affected': count})
    return {'status': 'withdrawn', 'withdrawn_count': count}


@app.get('/api/privacy/consent/{principal_id}')
def consent_list(principal_id: str, user=Depends(current_user)):
    """Lists current consents for a Data Principal."""
    from core.privacy.consent import list_consents
    with scoped(user['tenant_id']) as conn:
        consents = list_consents(conn, user['tenant_id'], principal_id)
    return {'data_principal_id': principal_id, 'consents': consents}


@app.post('/api/privacy/dsar')
def dsar_export(body: PrincipalInput, user=Depends(current_user)):
    """Exports Data Principal summary per Section 11 of the DPDP Act."""
    from core.privacy.rights import export_data_principal_summary
    rate_limit('privacy-dsar:'+str(user['id']), 10, 60)
    with scoped(user['tenant_id']) as conn:
        summary = export_data_principal_summary(conn, user['tenant_id'], body.data_principal_id)
        audit(conn, user['tenant_id'], None, 'privacy.dsar_exported', 'completed', actor=user['id'],
              details={'data_principal_id': body.data_principal_id})
    return summary


@app.post('/api/privacy/erase')
def privacy_erase(body: PrincipalInput, user=Depends(current_user)):
    """Executes Right to Erasure / Right to be Forgotten per Section 12(3) of DPDP Act."""
    require(user, 'owner', 'operator')
    from core.privacy.rights import execute_erasure
    with scoped(user['tenant_id']) as conn:
        result = execute_erasure(conn, user['tenant_id'], body.data_principal_id)
        audit(conn, user['tenant_id'], None, 'privacy.erasure_executed', 'completed', actor=user['id'],
              details={'data_principal_id': body.data_principal_id, 'anonymized_reference': result.get('anonymized_reference')})
    return result


@app.post('/api/privacy/grievance')
def file_grievance(body: GrievanceInput, user=Depends(current_user)):
    """Registers a formal Data Principal privacy grievance under Section 13."""
    grievance_id = uuid4()
    with scoped(user['tenant_id']) as conn:
        conn.execute(
            """
            INSERT INTO privacy_grievances (id, tenant_id, data_principal_id, subject, description, status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'open', now())
            """,
            (grievance_id, user['tenant_id'], body.data_principal_id, body.subject, body.description)
        )
        audit(conn, user['tenant_id'], None, 'privacy.grievance_filed', 'open', actor=user['id'],
              details={'grievance_id': str(grievance_id), 'data_principal_id': body.data_principal_id})
    return {
        'status': 'registered',
        'grievance_id': str(grievance_id),
        'statutory_notice': 'Grievance registered. Under DPDP Act Section 13, the DPO will respond within prescribed timelines.'
    }


@app.post('/api/privacy/breach')
def report_breach(body: BreachInput, user=Depends(current_user)):
    """Logs a personal data breach and generates the DPBI regulatory notice per Section 8(6)."""
    require(user, 'owner')
    from core.privacy.breach import create_breach_report, format_dpbi_notification
    with scoped(user['tenant_id']) as conn:
        record = create_breach_report(
            conn, user['tenant_id'], body.incident_title, body.description,
            body.severity, body.affected_principals_count, body.categories_of_data
        )
        notification = format_dpbi_notification(record)
        audit(conn, user['tenant_id'], None, 'privacy.breach_reported', body.severity, actor=user['id'],
              details={'breach_id': record.get('id'), 'severity': body.severity})
    return {'breach_record': record, 'dpbi_regulatory_notification': notification}

