"""Signed custom-backend events feeding the existing approved sandbox workflows."""
import hashlib
import hmac
from typing import Literal
from uuid import UUID, uuid4, uuid5
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from core.clients.catalog import profile, require_connected, CATEGORIES
from core.config import settings
from core.db import auth_db, scoped
from core.runtime.audit import audit
from core.runtime.runs import create_run
from core.security.webhooks import master_key, key_id, endpoint_secret, valid_signature, MAX_BODY_BYTES


class EndpointInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=80)
    agent_id: UUID


class IncomingEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: UUID
    event_type: Literal['support.duplicate_charge', 'operations.review']
    customer_id: str | None = Field(default=None, pattern=r'^CUS-[0-9]+$')
    order_id: str | None = Field(default=None, pattern=r'^ORD-[0-9]+$')


def owner(user):
    if user['role'] != 'owner':
        raise HTTPException(403, 'Workspace owner required')


def configured_key():
    try:
        return master_key()
    except (OSError, ValueError):
        raise HTTPException(503, 'Webhook signing is not configured; contact the platform operator') from None


def public_endpoint(row):
    return {k: row[k] for k in ('id', 'agent_id', 'name', 'created_at', 'rotated_at', 'revoked_at')}


def issued(row, key):
    return {**public_endpoint(row), 'path': '/api/webhooks/'+str(row['id']),
            'secret': endpoint_secret(row, key), 'mode': 'sandbox',
            'notice': 'Copy the signing secret now. It is shown only on creation or rotation.'}


def install_webhook_routes(app, current_user, rate_limit):
    @app.get('/api/workspace/webhooks')
    def list_endpoints(user=Depends(current_user)):
        with auth_db() as conn:
            rows = conn.execute('SELECT * FROM auth_private.webhook_endpoints WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 100', (user['tenant_id'],)).fetchall()
        try:
            fingerprint = key_id(master_key())
        except (OSError, ValueError):
            fingerprint = None
        return {'mode': 'sandbox', 'signing_configured': fingerprint is not None,
                'endpoints': [{**public_endpoint(row), 'needs_rotation': bool(fingerprint and fingerprint != row['master_key_id']),
                               'path': '/api/webhooks/'+str(row['id'])} for row in rows]}

    @app.post('/api/workspace/webhooks', status_code=201)
    def new_endpoint(body: EndpointInput, user=Depends(current_user)):
        owner(user)
        rate_limit('webhook-create:'+str(user['tenant_id']), 20, 3600)
        key = configured_key()
        with scoped(user['tenant_id']) as conn:
            if not profile(conn):
                raise HTTPException(409, 'Webhooks currently require a managed sandbox workspace')
            if not conn.execute('SELECT id FROM agents WHERE id=%s', (body.agent_id,)).fetchone():
                raise HTTPException(422, 'Agent does not belong to this workspace')
        with auth_db() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,731))', (str(user['tenant_id']),))
            count = conn.execute('SELECT count(*) AS n FROM auth_private.webhook_endpoints WHERE tenant_id=%s AND revoked_at IS NULL', (user['tenant_id'],)).fetchone()['n']
            if count >= 20:
                raise HTTPException(409, 'A workspace can have at most 20 active webhook endpoints')
            row = conn.execute('''INSERT INTO auth_private.webhook_endpoints(id,tenant_id,agent_id,created_by,name,generation,master_key_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (uuid4(), user['tenant_id'], body.agent_id, user['id'], body.name, uuid4(), key_id(key))).fetchone()
        return issued(row, key)

    @app.post('/api/workspace/webhooks/{endpoint_id}/rotate', status_code=201)
    def rotate(endpoint_id: UUID, user=Depends(current_user)):
        owner(user)
        rate_limit('webhook-rotate:'+str(user['tenant_id']), 30, 3600)
        key = configured_key()
        with auth_db() as conn:
            row = conn.execute('''UPDATE auth_private.webhook_endpoints SET generation=%s,master_key_id=%s,rotated_at=now()
                WHERE id=%s AND tenant_id=%s AND revoked_at IS NULL RETURNING *''',
                (uuid4(), key_id(key), endpoint_id, user['tenant_id'])).fetchone()
            if not row:
                raise HTTPException(404, 'Active webhook endpoint not found')
        return issued(row, key)

    @app.delete('/api/workspace/webhooks/{endpoint_id}')
    def revoke(endpoint_id: UUID, user=Depends(current_user)):
        owner(user)
        with auth_db() as conn:
            row = conn.execute('''UPDATE auth_private.webhook_endpoints SET revoked_at=COALESCE(revoked_at,now())
                WHERE id=%s AND tenant_id=%s RETURNING id''', (endpoint_id, user['tenant_id'])).fetchone()
            if not row:
                raise HTTPException(404, 'Webhook endpoint not found')
        return {'status': 'revoked', 'notice': 'Future deliveries are blocked; already accepted runs are retained.'}

    @app.get('/api/workspace/webhooks/{endpoint_id}/events')
    def events(endpoint_id: UUID, user=Depends(current_user)):
        with auth_db() as conn:
            endpoint = conn.execute('SELECT id FROM auth_private.webhook_endpoints WHERE id=%s AND tenant_id=%s', (endpoint_id, user['tenant_id'])).fetchone()
            if not endpoint:
                raise HTTPException(404, 'Webhook endpoint not found')
        with scoped(user['tenant_id']) as conn:
            return conn.execute('''SELECT event_id,event_type,outcome,reason_code,run_id,attempts,received_at,last_received_at
                FROM webhook_events WHERE endpoint_id=%s ORDER BY last_received_at DESC LIMIT 100''', (endpoint_id,)).fetchall()

    def receive(endpoint_id, raw, timestamp, signature):
        rate_limit('webhook-receive:'+str(endpoint_id), 120, 60)
        key = configured_key()
        # Row lock makes rotation/revocation wait for in-flight accepted submissions.
        with auth_db() as auth:
            endpoint = auth.execute('''SELECT e.* FROM auth_private.webhook_endpoints e
                JOIN auth_private.users u ON u.id=e.created_by AND u.tenant_id=e.tenant_id
                WHERE e.id=%s AND e.revoked_at IS NULL AND u.active AND u.role='owner' FOR SHARE OF e''', (endpoint_id,)).fetchone()
            if not endpoint:
                raise HTTPException(401, 'Webhook endpoint or signature is invalid')
            if endpoint['master_key_id'] != key_id(key):
                raise HTTPException(503, 'Endpoint signing key requires owner rotation')
            if not valid_signature(endpoint_secret(endpoint, key), timestamp, signature, raw):
                raise HTTPException(401, 'Webhook endpoint or signature is invalid or expired')
            try:
                event = IncomingEvent.model_validate_json(raw)
            except ValidationError:
                raise HTTPException(422, 'Invalid event schema; use a supported event type and event UUID') from None
            return submit_event(endpoint, event, hashlib.sha256(raw).hexdigest())

    def submit_event(endpoint, event, digest):
        with scoped(endpoint['tenant_id'], endpoint['agent_id']) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (str(endpoint['agent_id']),))
            previous = conn.execute('SELECT * FROM webhook_events WHERE endpoint_id=%s AND event_id=%s', (endpoint['id'], event.event_id)).fetchone()
            if previous and not hmac.compare_digest(previous['payload_digest'], digest):
                return JSONResponse({'detail': 'event_id already used with different bytes'}, status_code=409)
            if previous and previous['outcome'] == 'accepted':
                conn.execute('UPDATE webhook_events SET attempts=attempts+1,last_received_at=now() WHERE endpoint_id=%s AND event_id=%s', (endpoint['id'], event.event_id))
                return JSONResponse({'event_id': str(event.event_id), 'run_id': str(previous['run_id']), 'replayed': True, 'mode': 'sandbox'}, status_code=202)
            rate_limit('runs:'+str(endpoint['tenant_id'])+':'+str(endpoint['agent_id']), settings.max_runs_per_hour, 3600)
            agent = conn.execute('SELECT * FROM agents WHERE id=%s', (endpoint['agent_id'],)).fetchone()
            expected = 'support.duplicate_charge' if agent and agent['kind'] == 'support' else 'operations.review'
            reason, inputs, run = None, {}, None
            if not agent or not profile(conn):
                reason = 'workspace_unavailable'
            elif event.event_type != expected:
                reason = 'event_agent_mismatch'
            elif agent['kind'] == 'support' and (not event.customer_id or not event.order_id):
                reason = 'customer_and_order_required'
            elif agent['kind'] == 'operations' and (event.customer_id or event.order_id):
                reason = 'unexpected_customer_or_order'
            if not reason:
                try:
                    for category in CATEGORIES:
                        require_connected(conn, category)
                except ValueError:
                    reason = 'sandbox_connections_required'
            if not reason and agent['kind'] == 'support':
                inputs = {'customer_id': event.customer_id, 'order_id': event.order_id}
                from simulations.ecommerce.store import investigate
                try:
                    investigate(conn, inputs)
                except ValueError:
                    reason = 'customer_order_binding_invalid'
            if not reason:
                objective = 'Investigate the reported duplicate charge.' if agent['kind'] == 'support' else 'Review daily business operations.'
                try:
                    run = create_run(conn, {'id': endpoint['created_by'], 'tenant_id': endpoint['tenant_id']},
                                     agent, objective, uuid5(endpoint['id'], str(event.event_id)), inputs)
                except ValueError:
                    reason = 'workflow_not_ready_or_consent_missing'
            outcome = 'rejected' if reason else 'accepted'
            conn.execute('''INSERT INTO webhook_events(tenant_id,agent_id,endpoint_id,event_id,event_type,payload_digest,outcome,reason_code,run_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(tenant_id,endpoint_id,event_id) DO UPDATE SET outcome=EXCLUDED.outcome,reason_code=EXCLUDED.reason_code,
                run_id=EXCLUDED.run_id,attempts=webhook_events.attempts+1,last_received_at=now()''',
                (endpoint['tenant_id'], endpoint['agent_id'], endpoint['id'], event.event_id, event.event_type, digest, outcome, reason, run['id'] if run else None))
            audit(conn, endpoint['tenant_id'], endpoint['agent_id'], 'webhook.'+outcome, outcome,
                  actor='webhook:'+str(endpoint['id']), run=run['id'] if run else None,
                  details={'event_id': str(event.event_id), 'event_type': event.event_type, 'reason_code': reason})
            if reason:
                return JSONResponse({'event_id': str(event.event_id), 'detail': reason, 'mode': 'sandbox'}, status_code=409)
            return JSONResponse({'event_id': str(event.event_id), 'run_id': str(run['id']), 'replayed': False, 'mode': 'sandbox'}, status_code=202)

    @app.post('/api/webhooks/{endpoint_id}')
    async def webhook(endpoint_id: UUID, request: Request):
        source = request.client.host if request.client else 'unknown'
        await run_in_threadpool(rate_limit, 'webhook-source:'+hashlib.sha256(source.encode()).hexdigest(), 600, 60)
        if request.headers.get('origin') or request.cookies.get('wf_session'):
            raise HTTPException(403, 'Webhook delivery is server-to-server only')
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise HTTPException(415, 'application/json is required')
        if request.headers.get('content-encoding', 'identity') != 'identity':
            raise HTTPException(415, 'Send the uncompressed signed JSON body')
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk) > MAX_BODY_BYTES:
                raise HTTPException(413, 'Webhook body exceeds 32 KB')
            raw.extend(chunk)
        return await run_in_threadpool(receive, endpoint_id, bytes(raw),
                                      request.headers.get('x-workforce-timestamp', ''), request.headers.get('x-workforce-signature', ''))
