"""Client-facing routes. Cookie management and service-key execution stay separate."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg import errors
from psycopg.types.json import Jsonb
from core.db import auth_db, scoped
from core.config import settings
from core.policies.engine import PolicyRules
from core.security.passwords import hash_password, token_hash
from core.security.pii import redact_pii
from core.tools.registry import tool_catalog
from core.runtime.audit import audit
from core.runtime.runs import create_run
from core.clients.catalog import CATEGORIES, profile, require_connected

from typing import Literal

Category = Literal['ecommerce','custom','crm']
Scope = Literal['runs:write','runs:read','integrations:read']
MemberRole = Literal['operator','finance_approver','viewer']

class Body(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class Signup(Body):
    business_name: str = Field(min_length=2,max_length=120)
    owner_name: str = Field(min_length=2,max_length=100)
    email: str = Field(min_length=5,max_length=254,pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    password: str = Field(min_length=12,max_length=128)
    category: Category

class Invite(Body):
    email: str = Field(min_length=5,max_length=254,pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    role: MemberRole

class Accept(Body):
    token: str = Field(min_length=40,max_length=150)
    name: str = Field(min_length=2,max_length=100)
    password: str = Field(min_length=12,max_length=128)

class KeyInput(Body):
    name: str = Field(min_length=2,max_length=80)
    scopes: list[Scope] = Field(min_length=1,max_length=3)
    agent_ids: list[UUID] = Field(min_length=1,max_length=20)
    expires_in_days: int = Field(default=30,ge=1,le=90)
    rate_per_minute: int = Field(default=60,ge=1,le=300)

class Connect(Body):
    load_demo_data: bool = False

class SubmitRun(Body):
    agent_id: UUID
    request_key: UUID
    objective: str = Field(min_length=3,max_length=4000)
    customer_id: str | None = Field(default=None,pattern=r'^CUS-[0-9]+$')
    order_id: str | None = Field(default=None,pattern=r'^ORD-[0-9]+$')
    _redact=field_validator('objective')(redact_pii)


def definition():
    result=[]
    for kind in ('operations','support'):
        tools=tool_catalog(kind)
        extra={'InventorySkill','ShipmentSkill','PaymentSkill'} if kind=='operations' else {'CustomerLookupSkill','OrderLookupSkill','AbuseSignalSkill','EscalationSkill'}
        result.append({'id':str(uuid4()),'kind':kind,'name':'Operations Agent' if kind=='operations' else 'Support Agent',
                       'rules':PolicyRules().model_dump(),'skills':sorted({t.skill for t in tools}|extra),
                       'tools':[{'name':t.name} for t in tools]})
    return result


def owner(user):
    if user['role']!='owner': raise HTTPException(403,'Workspace owner required')


def token_value(prefix):
    return prefix+secrets.token_urlsafe(48)


def issue_key(conn,user,body,expires_at=None):
    secret=token_value('wf_test_'); key_id=uuid4()
    expires_at=expires_at or datetime.now(timezone.utc)+timedelta(days=body.expires_in_days)
    conn.execute('''INSERT INTO auth_private.client_api_keys(id,tenant_id,issued_by,name,token_hash,prefix,scopes,agent_ids,rate_per_minute,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (key_id,user['tenant_id'],user['id'],body.name,token_hash(secret),secret[:16],sorted(set(body.scopes)),list(set(body.agent_ids)),body.rate_per_minute,expires_at))
    return {'id':str(key_id),'token':secret,'expires_at':expires_at,'notice':'Copy now. This secret will not be shown again. Server-side use only.'}


def install_client_routes(app,current_user,rate_limit):
    @app.get('/api/onboarding/options')
    def options():
        enabled=os.getenv('WORKSPACE_SIGNUP_ENABLED','false' if settings.app_env=='production' else 'true')=='true'
        return {'signup_enabled':enabled,'mode':'sandbox','categories':CATEGORIES,'invites_delivered_automatically':False}

    @app.post('/api/workspaces',status_code=201)
    def signup(body:Signup,request:Request):
        if not options()['signup_enabled']: raise HTTPException(403,'Workspace signup is disabled on this deployment')
        rate_limit('signup:'+token_hash(request.client.host if request.client else 'unknown'),5,3600)
        tenant_id,owner_id=uuid4(),uuid4()
        try:
            with auth_db() as conn:
                conn.execute('SELECT auth_private.provision_workspace(%s,%s,%s,%s,%s,%s,%s,%s)',
                    (tenant_id,owner_id,body.business_name,body.email.lower(),body.owner_name,hash_password(body.password),body.category,Jsonb(definition())))
        except errors.UniqueViolation:
            raise HTTPException(409,'Unable to create this account. Use a different email or sign in.') from None
        return {'tenant_id':tenant_id,'mode':'sandbox','next':'Sign in and connect the sandbox systems in Integrations.'}

    @app.post('/api/invitations/accept',status_code=201)
    def accept(body:Accept,request:Request):
        rate_limit('invite-accept:'+token_hash(request.client.host if request.client else 'unknown'),10,3600)
        try:
            with auth_db() as conn:
                conn.execute('SELECT auth_private.accept_workspace_invitation(%s,%s,%s)',(token_hash(body.token),body.name,hash_password(body.password)))
        except (errors.RaiseException,errors.UniqueViolation):
            raise HTTPException(409,'Invitation unavailable, expired, already used, or account already exists') from None
        return {'status':'accepted','next':'Sign in with the invited email address.'}

    @app.get('/api/workspace')
    def workspace(user=Depends(current_user)):
        with scoped(user['tenant_id']) as conn:
            tenant=conn.execute('SELECT id,name FROM tenants').fetchone()
            current=profile(conn)
            agents=conn.execute('SELECT id,name,kind,autonomy FROM agents ORDER BY kind').fetchall()
            connections=conn.execute('SELECT * FROM workspace_connections ORDER BY category').fetchall()
        return {'tenant':tenant,'profile':current,'agents':agents,'connections':connections,'catalog':CATEGORIES,
                'limits':{'mode':'sandbox' if current else 'legacy','live_vendor_connections':False,'widget_available':False}}

    @app.get('/api/workspace/members')
    def members(user=Depends(current_user)):
        owner(user)
        with auth_db() as conn:
            people=conn.execute('SELECT id,name,email,role,active FROM auth_private.users WHERE tenant_id=%s ORDER BY name',(user['tenant_id'],)).fetchall()
            invites=conn.execute('SELECT id,email,role,expires_at,accepted_at,revoked_at FROM auth_private.workspace_invitations WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100',(user['tenant_id'],)).fetchall()
        return {'members':people,'invitations':invites}

    @app.post('/api/workspace/invitations',status_code=201)
    def invite(body:Invite,user=Depends(current_user)):
        owner(user); rate_limit('invites:'+str(user['tenant_id']),20,3600)
        secret=token_value('wfi_'); invitation_id=uuid4()
        with auth_db() as conn:
            conn.execute('''INSERT INTO auth_private.workspace_invitations(id,tenant_id,invited_by,email,role,token_hash,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,now()+interval '48 hours')''',(invitation_id,user['tenant_id'],user['id'],body.email.lower(),body.role,token_hash(secret)))
        return {'id':invitation_id,'token':secret,'expires_in_hours':48,'delivery':'Copy the invitation link and share it yourself. No message was sent.'}

    @app.delete('/api/workspace/invitations/{invitation_id}')
    def revoke_invite(invitation_id:UUID,user=Depends(current_user)):
        owner(user)
        with auth_db() as conn:
            row=conn.execute('UPDATE auth_private.workspace_invitations SET revoked_at=now() WHERE id=%s AND tenant_id=%s AND accepted_at IS NULL RETURNING id',(invitation_id,user['tenant_id'])).fetchone()
            if not row: raise HTTPException(404,'Active invitation not found')
        return {'status':'revoked'}

    @app.post('/api/workspace/connections/{category}/connect')
    def connect(category:Category,body:Connect,user=Depends(current_user)):
        owner(user)
        if not body.load_demo_data: raise HTTPException(422,'Explicitly confirm loading fictional sandbox records')
        from simulations.ecommerce.seed import seed_agent_records
        with scoped(user['tenant_id']) as conn:
            if not profile(conn): raise HTTPException(409,'Create a new sandbox workspace to use these integrations')
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,904))',(str(user['tenant_id']),))
            agents=conn.execute('SELECT id,kind FROM agents ORDER BY id').fetchall()
            for agent in agents:
                conn.execute("SELECT set_config('app.agent_id',%s,true)",(str(agent['id']),))
                if not conn.execute('SELECT 1 FROM sim_records LIMIT 1').fetchone():
                    seed_agent_records(conn,user['tenant_id'],agent['id'],agent['kind'])
            conn.execute("SELECT set_config('app.agent_id','control',true)")
            conn.execute("UPDATE workspace_connections SET status='connected',checked_at=NULL,check_result='{}' WHERE category=%s",(category,))
            audit(conn,user['tenant_id'],None,'integration.connected','sandbox',actor=user['id'],details={'category':category,'fictional_data':True})
        return {'status':'connected','mode':'sandbox','notice':'Fictional records only; no external account connected.'}

    @app.post('/api/workspace/connections/{category}/test')
    def check_connection(category:Category,user=Depends(current_user)):
        owner(user)
        with scoped(user['tenant_id']) as conn:
            try: require_connected(conn,category)
            except ValueError as exc: raise HTTPException(409,str(exc)) from None
            count=conn.execute('SELECT count(*) AS n FROM sim_records').fetchone()['n']
            checks={'mode':'sandbox','tenant_scope':True,'records_available':count>0,'external_credentials_tested':False}
            conn.execute('UPDATE workspace_connections SET checked_at=now(),check_result=%s WHERE category=%s',(Jsonb(checks),category))
        return checks

    @app.delete('/api/workspace/connections/{category}')
    def disconnect(category:Category,user=Depends(current_user)):
        owner(user)
        with scoped(user['tenant_id']) as conn:
            from core.privacy.consent import consent_lock
            consent_lock(conn,user['tenant_id'],exclusive=True)
            row=conn.execute("UPDATE workspace_connections SET status='disconnected',checked_at=NULL,check_result='{}' WHERE category=%s RETURNING id",(category,)).fetchone()
            if not row: raise HTTPException(404,'Connection not found')
            audit(conn,user['tenant_id'],None,'integration.disconnected','completed',actor=user['id'],details={'category':category})
        return {'status':'disconnected','notice':'Future calls are blocked; existing records are retained.'}

    @app.get('/api/workspace/api-keys')
    def list_keys(user=Depends(current_user)):
        owner(user)
        with auth_db() as conn:
            return conn.execute('''SELECT id,name,prefix,scopes,agent_ids,rate_per_minute,created_at,expires_at,revoked_at,last_used_at
                FROM auth_private.client_api_keys WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100''',(user['tenant_id'],)).fetchall()

    @app.post('/api/workspace/api-keys',status_code=201)
    def new_key(body:KeyInput,user=Depends(current_user)):
        owner(user);rate_limit('key-issue:'+str(user['tenant_id']),20,3600)
        with scoped(user['tenant_id']) as conn:
            if not profile(conn): raise HTTPException(409,'Create a sandbox workspace before issuing client keys')
            agent_ids={r['id'] for r in conn.execute('SELECT id FROM agents').fetchall()}
            if not set(body.agent_ids)<=agent_ids: raise HTTPException(422,'Agent does not belong to this workspace')
        with auth_db() as conn: return issue_key(conn,user,body)

    @app.delete('/api/workspace/api-keys/{key_id}')
    def revoke_key(key_id:UUID,user=Depends(current_user)):
        owner(user)
        with auth_db() as conn:
            row=conn.execute('UPDATE auth_private.client_api_keys SET revoked_at=COALESCE(revoked_at,now()) WHERE id=%s AND tenant_id=%s RETURNING id',(key_id,user['tenant_id'])).fetchone()
            if not row: raise HTTPException(404,'Key not found')
        return {'status':'revoked'}

    @app.post('/api/workspace/api-keys/{key_id}/rotate',status_code=201)
    def rotate_key(key_id:UUID,user=Depends(current_user)):
        owner(user)
        with auth_db() as conn:
            old=conn.execute('SELECT * FROM auth_private.client_api_keys WHERE id=%s AND tenant_id=%s AND revoked_at IS NULL AND expires_at>now() FOR UPDATE',(key_id,user['tenant_id'])).fetchone()
            if not old: raise HTTPException(404,'Active key not found')
            body=KeyInput(name=old['name'],scopes=old['scopes'],agent_ids=old['agent_ids'],rate_per_minute=old['rate_per_minute'])
            result=issue_key(conn,user,body,old['expires_at'])
            conn.execute('UPDATE auth_private.client_api_keys SET revoked_at=now() WHERE id=%s',(key_id,))
        return result

    def api_identity(request:Request):
        if request.headers.get('origin') or request.cookies.get('wf_session'):
            raise HTTPException(403,'Client keys are for server-to-server calls, not browser sessions')
        authorization=request.headers.get('authorization','')
        if not authorization.startswith('Bearer wf_test_') or len(authorization)>160:
            raise HTTPException(401,'Valid client Bearer key required')
        raw=authorization[7:]
        with auth_db() as conn:
            key=conn.execute('''SELECT k.*,u.active FROM auth_private.client_api_keys k JOIN auth_private.users u ON u.id=k.issued_by AND u.tenant_id=k.tenant_id
                WHERE k.token_hash=%s AND k.revoked_at IS NULL AND k.expires_at>now() AND u.active''',(token_hash(raw),)).fetchone()
            if not key: raise HTTPException(401,'Client key invalid, expired or revoked')
            conn.execute('UPDATE auth_private.client_api_keys SET last_used_at=now() WHERE id=%s',(key['id'],))
        rate_limit('client-key:'+str(key['id']),key['rate_per_minute'],60)
        return key

    def authorize(key,scope,agent_id=None):
        if scope not in key['scopes'] or (agent_id is not None and agent_id not in key['agent_ids']):
            raise HTTPException(403,'Client key does not permit this scope or agent')

    @app.get('/api/v1/integrations')
    def api_integrations(key=Depends(api_identity)):
        authorize(key,'integrations:read')
        with scoped(key['tenant_id']) as conn:
            return {'mode':'sandbox','connections':conn.execute('SELECT category,provider,status,checked_at,check_result FROM workspace_connections ORDER BY category').fetchall()}

    @app.post('/api/v1/runs',status_code=202)
    def submit(body:SubmitRun,key=Depends(api_identity)):
        authorize(key,'runs:write',body.agent_id)
        payload_digest=hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        with scoped(key['tenant_id'],body.agent_id) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(body.agent_id),))
            previous=conn.execute('SELECT * FROM api_submissions WHERE agent_id=%s AND request_key=%s',(body.agent_id,body.request_key)).fetchone()
            if previous:
                if not hmac.compare_digest(previous['payload_digest'],payload_digest):
                    raise HTTPException(409,'request_key was already used for a different payload')
                run=conn.execute('SELECT id,status FROM agent_runs WHERE id=%s',(previous['run_id'],)).fetchone()
                return {'run_id':run['id'],'status':run['status'],'status_url':'/api/v1/runs/'+str(run['id']),'replayed':True}
            rate_limit('runs:'+str(key['tenant_id'])+':'+str(body.agent_id),settings.max_runs_per_hour,3600)
            agent=conn.execute('SELECT * FROM agents WHERE id=%s',(body.agent_id,)).fetchone()
            if not agent: raise HTTPException(404,'Agent not found')
            inputs={}
            try:
                # Full dependency readiness before accepting a new client job.
                for category in CATEGORIES: require_connected(conn,category)
                if agent['kind']=='support':
                    if not body.customer_id or not body.order_id: raise ValueError('customer_id and order_id are required')
                    inputs={'customer_id':body.customer_id,'order_id':body.order_id}
                    from simulations.ecommerce.store import investigate
                    investigate(conn,inputs)
                run=create_run(conn,{'id':key['issued_by'],'tenant_id':key['tenant_id']},agent,body.objective,body.request_key,inputs)
            except ValueError as exc: raise HTTPException(409,str(exc)) from None
            conn.execute('INSERT INTO api_submissions(tenant_id,agent_id,request_key,api_key_id,payload_digest,run_id) VALUES(%s,%s,%s,%s,%s,%s)',
                (key['tenant_id'],body.agent_id,body.request_key,key['id'],payload_digest,run['id']))
            audit(conn,key['tenant_id'],body.agent_id,'client.run_submitted','queued',actor='api-key:'+str(key['id']),run=run['id'])
        return {'run_id':run['id'],'status':run['status'],'status_url':'/api/v1/runs/'+str(run['id']),'replayed':False}

    @app.get('/api/v1/runs/{run_id}')
    def api_run(run_id:UUID,key=Depends(api_identity)):
        authorize(key,'runs:read')
        with scoped(key['tenant_id']) as conn:
            run=conn.execute('SELECT id,agent_id,status,summary,error,created_at,finished_at FROM agent_runs WHERE id=%s',(run_id,)).fetchone()
            if not run: raise HTTPException(404,'Run not found')
            authorize(key,'runs:read',run['agent_id'])
            tasks=conn.execute('SELECT id,tool,status,verification,reason FROM agent_tasks WHERE run_id=%s ORDER BY position',(run_id,)).fetchall()
        return {'run':run,'tasks':tasks,'mode':'sandbox','approvals':'Human approvers must use the dashboard; API keys cannot approve.'}
