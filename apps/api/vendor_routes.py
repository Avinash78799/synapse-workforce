"""Owner-managed, agent-scoped vendor installations. Inspection only in this milestone."""
from typing import Literal
from uuid import UUID, uuid4
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb
from core.db import scoped
from core.runtime.audit import audit
from core.security.secrets import VaultSecrets
from core.connectors.vendor_readers import hostname, check_account, read_resource, VendorError


class InstallationInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    vendor: Literal['shopify','zendesk']
    account: str = Field(min_length=1,max_length=63)
    agent_id: UUID


class ResourceInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    resource_id: str = Field(pattern=r'^[1-9][0-9]{0,19}$')


def owner(user):
    if user['role']!='owner': raise HTTPException(403,'Workspace owner required')


def secret_path(row):
    return f"tenants/{row['tenant_id']}/agents/{row['agent_id']}/integrations/{row['secret_ref']}"


def install_vendor_routes(app,current_user,rate_limit):
    @app.get('/api/workspace/vendors')
    def list_installations(user=Depends(current_user)):
        owner(user)
        with scoped(user['tenant_id']) as conn:
            rows=conn.execute('SELECT * FROM vendor_installations ORDER BY created_at DESC LIMIT 100').fetchall()
        return {'mode':'vendor_read_only','workflow_execution_enabled':False,
                'installations':[{**r,'credential_path':secret_path(r)} for r in rows]}

    @app.post('/api/workspace/vendors',status_code=201)
    def register(body:InstallationInput,user=Depends(current_user)):
        owner(user);rate_limit('vendor-register:'+str(user['tenant_id']),20,3600)
        account=body.account.lower()
        try: hostname(body.vendor,account)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        with scoped(user['tenant_id'],body.agent_id) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,732))',(str(body.agent_id),))
            agent=conn.execute('SELECT id FROM agents WHERE id=%s',(body.agent_id,)).fetchone()
            if not agent:raise HTTPException(404,'Agent not found')
            if conn.execute('SELECT id FROM vendor_installations WHERE vendor=%s AND account=%s AND revoked_at IS NULL',(body.vendor,account)).fetchone():
                raise HTTPException(409,'An active installation already exists for this agent and account')
            if conn.execute('SELECT count(*) AS n FROM vendor_installations WHERE revoked_at IS NULL').fetchone()['n']>=20:
                raise HTTPException(409,'At most 20 active vendor installations are allowed per agent')
            installation,reference=uuid4(),uuid4()
            conn.execute('INSERT INTO integrations(id,tenant_id,agent_id,name,status) VALUES(%s,%s,%s,%s,%s)',
                         (installation,user['tenant_id'],body.agent_id,'vendor:'+str(installation),'pending_credentials'))
            conn.execute('INSERT INTO credentials(id,tenant_id,agent_id,integration_id,secret_ref) VALUES(%s,%s,%s,%s,%s)',
                         (uuid4(),user['tenant_id'],body.agent_id,installation,reference))
            row=conn.execute('''INSERT INTO vendor_installations(id,tenant_id,agent_id,vendor,account,secret_ref)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING *''',(installation,user['tenant_id'],body.agent_id,body.vendor,account,reference)).fetchone()
            audit(conn,user['tenant_id'],body.agent_id,'vendor.registered','pending_credentials',actor=user['id'],details={'installation_id':str(installation),'vendor':body.vendor})
        return {**row,'credential_path':secret_path(row),'workflow_execution_enabled':False}

    @app.delete('/api/workspace/vendors/{installation_id}')
    def revoke(installation_id:UUID,user=Depends(current_user)):
        owner(user)
        with scoped(user['tenant_id']) as conn:
            oauth=conn.execute('SELECT 1 FROM vendor_oauth_connections WHERE installation_id=%s',(installation_id,)).fetchone()
        if oauth:
            from core.connectors.oauth_lifecycle import disconnect
            from core.connectors.zendesk_oauth import OAuthError
            try:
                status=disconnect(user['tenant_id'],installation_id,user['id'])
            except OAuthError as exc:
                raise HTTPException(409,exc.code) from None
            return {'status':status,'notice':'Platform access blocked. '+('Zendesk revocation confirmed.' if status=='revoked' else 'Zendesk revocation needs operator verification.')}
        with scoped(user['tenant_id']) as conn:
            row=conn.execute("UPDATE vendor_installations SET status='revoked',revoked_at=COALESCE(revoked_at,now()) WHERE id=%s RETURNING *",(installation_id,)).fetchone()
            if not row:raise HTTPException(404,'Installation not found')
            conn.execute("UPDATE integrations SET status='revoked' WHERE id=%s",(installation_id,))
            # Use the same agent audit chain as its other actions.
            conn.execute("SELECT set_config('app.agent_id',%s,true)",(str(row['agent_id']),))
            audit(conn,user['tenant_id'],row['agent_id'],'vendor.revoked','revoked',actor=user['id'],details={'installation_id':str(installation_id)})
        return {'status':'revoked','notice':'Platform reads are blocked. Revoke the token with the vendor and remove its Vault value separately.'}

    def inspect(installation_id,user,resource_id=None):
        owner(user);rate_limit('vendor-read:'+str(user['tenant_id']),60,60)
        with scoped(user['tenant_id']) as conn:
            # Serializes inspection and revocation; no cached credentials survive a later call.
            row=conn.execute('SELECT * FROM vendor_installations WHERE id=%s FOR UPDATE',(installation_id,)).fetchone()
            if not row:raise HTTPException(404,'Installation not found')
            if row['revoked_at']:raise HTTPException(409,'Installation revoked')
            conn.execute("SELECT set_config('app.agent_id',%s,true)",(str(row['agent_id']),))
            agent=conn.execute('SELECT status FROM agents WHERE id=%s FOR SHARE',(row['agent_id'],)).fetchone()
            if not agent or agent['status']!='active':raise HTTPException(409,'Agent is not active')
            try:
                try:token=VaultSecrets().integration_key(conn,user['tenant_id'],row['agent_id'],row['secret_ref'])
                except (RuntimeError,PermissionError,OSError,ValueError):raise VendorError('credential_unavailable') from None
                result=read_resource(row['vendor'],row['account'],token,resource_id) if resource_id else check_account(row['vendor'],row['account'],token)
            except VendorError as exc:
                conn.execute("UPDATE vendor_installations SET status='check_failed',checked_at=now(),last_check=%s WHERE id=%s",(Jsonb({'code':exc.code,'operation':'resource' if resource_id else 'account'}),installation_id))
                conn.execute("UPDATE integrations SET status='check_failed' WHERE id=%s",(installation_id,))
                audit(conn,user['tenant_id'],row['agent_id'],'vendor.read','failed',actor=user['id'],details={'installation_id':str(installation_id),'code':exc.code})
                return JSONResponse({'detail':exc.code,'workflow_execution_enabled':False},status_code=503 if exc.code in {'credential_unavailable','vendor_unavailable','vendor_rate_limited'} else 502)
            if not resource_id:
                conn.execute("UPDATE vendor_installations SET status='read_check_passed',checked_at=now(),last_check=%s WHERE id=%s",(Jsonb(result),installation_id))
                conn.execute("UPDATE integrations SET status='read_check_passed' WHERE id=%s",(installation_id,))
            audit(conn,user['tenant_id'],row['agent_id'],'vendor.read','completed',actor=user['id'],details={'installation_id':str(installation_id),'operation':'resource' if resource_id else 'account'})
        return {'installation_id':str(installation_id),'result':result,'workflow_execution_enabled':False}

    @app.post('/api/workspace/vendors/{installation_id}/check')
    def check(installation_id:UUID,user=Depends(current_user)):
        return inspect(installation_id,user)

    @app.post('/api/workspace/vendors/{installation_id}/inspect')
    def resource(installation_id:UUID,body:ResourceInput,user=Depends(current_user)):
        return inspect(installation_id,user,body.resource_id)
