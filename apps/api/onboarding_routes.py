from uuid import UUID
from fastapi import Depends, HTTPException
from psycopg.types.json import Jsonb
from core.clients.onboarding import ChecklistInput, STAGES, BLOCKED
from core.db import scoped, auth_db
from core.runtime.audit import audit


def is_founder(user):
    with auth_db() as conn:
        return bool(conn.execute('SELECT 1 FROM auth_private.platform_founders WHERE user_id=%s', (user['id'],)).fetchone())


def require_founder(user):
    if not is_founder(user):
        raise HTTPException(403, 'Internal onboarding access required')


def install_onboarding_routes(app, current_user, rate_limit):
    @app.get('/api/internal/onboarding/access')
    def access(user=Depends(current_user)):
        return {'allowed': is_founder(user)}

    @app.get('/api/internal/onboarding/workspaces')
    def workspaces(user=Depends(current_user)):
        require_founder(user)
        with auth_db() as conn:
            rows = conn.execute('SELECT * FROM public.founder_workspaces(%s)', (user['id'],)).fetchall()
        return {'workspaces': rows}

    @app.get('/api/internal/onboarding/{tenant_id}')
    def read(tenant_id: UUID, user=Depends(current_user)):
        require_founder(user)
        with scoped(tenant_id) as conn:
            tenant = conn.execute('SELECT id,name FROM tenants WHERE id=%s', (tenant_id,)).fetchone()
            if not tenant: raise HTTPException(404, 'Workspace not found')
            row = conn.execute('SELECT * FROM tenant_onboarding WHERE tenant_id=%s', (tenant_id,)).fetchone()
        return {'tenant': tenant, 'checklist': row or {'items': {}, 'notes': '', 'version': 0},
                'stages': [{'name': name, 'items': [{'key': k, 'label': label, 'help': help, 'blocked': BLOCKED.get(k)}
                           for k, label, help in items]} for name, items in STAGES]}

    @app.put('/api/internal/onboarding/{tenant_id}')
    def write(tenant_id: UUID, body: ChecklistInput, user=Depends(current_user)):
        require_founder(user)
        rate_limit('onboarding:' + str(user['id']), 60, 60)
        with scoped(tenant_id) as conn:
            tenant = conn.execute('SELECT id FROM tenants WHERE id=%s FOR UPDATE', (tenant_id,)).fetchone()
            if not tenant: raise HTTPException(404, 'Workspace not found')
            old = conn.execute('SELECT * FROM tenant_onboarding WHERE tenant_id=%s FOR UPDATE', (tenant_id,)).fetchone()
            if body.version != (old['version'] if old else 0):
                raise HTTPException(409, 'Someone updated this checklist. Reload it before saving.')
            items = {key: value.model_dump(mode='json') for key, value in body.items.items()}
            if items.get('zendesk_oauth',{}).get('status')=='done':
                connected=conn.execute("SELECT 1 FROM vendor_oauth_connections c JOIN vendor_installations v ON v.id=c.installation_id AND v.tenant_id=c.tenant_id WHERE c.status='active' AND c.expires_at>now() AND v.vendor='zendesk' AND v.revoked_at IS NULL LIMIT 1").fetchone()
                if not connected: raise HTTPException(422,'A current Zendesk OAuth connection is required to complete this item')
            if items.get('vendor_health',{}).get('status')=='done':
                checked=conn.execute("SELECT 1 FROM vendor_installations WHERE vendor='zendesk' AND status='read_check_passed' AND revoked_at IS NULL LIMIT 1").fetchone()
                if not checked: raise HTTPException(422,'A successful Zendesk account read check is required to complete this item')
            conn.execute('''INSERT INTO tenant_onboarding(tenant_id,items,notes,updated_by,version)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id) DO UPDATE SET items=EXCLUDED.items,
                notes=EXCLUDED.notes,updated_by=EXCLUDED.updated_by,version=EXCLUDED.version,updated_at=now()''',
                (tenant_id, Jsonb(items), body.notes, user['id'], body.version+1))
            # Notes are internal: only change counts enter the ordinary tenant audit feed.
            before = old['items'] if old else {}
            changed = sorted(k for k in set(before) | set(items) if before.get(k) != items.get(k))
            audit(conn, tenant_id, None, 'onboarding.updated', 'saved', actor=user['id'],
                  details={'changed_items': changed, 'version': body.version+1})
        return {'version': body.version+1}
