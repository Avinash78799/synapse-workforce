"""Plain-language projection of existing evidence; never manufacture a reason."""
from fastapi import Depends, Query
from core.db import scoped

TOOLS = {
    'support.investigate': 'Review the customer case', 'support.notify': 'Send the customer an update',
    'payment.refund': 'Refund a verified duplicate payment', 'payment.retry': 'Retry an eligible payment',
    'ticket.escalate': 'Refer the ticket to a person', 'helpdesk.escalate': 'Refer the ticket to a person',
    'operations.inspect': 'Review operational records', 'inventory.reorder': 'Request more stock',
    'shipment.alert': 'Flag a delivery problem',
    'ticket.update': 'Update the support ticket',
}


def describe(row):
    decision = row.get('decision')
    approval = row.get('approval_status')
    if approval == 'approved':
        permission = 'Approved by a person'
    elif approval == 'pending':
        permission = 'Waiting for a person' if not row.get('approval_expired') else 'Approval expired'
    elif approval in ('denied', 'expired'):
        permission = 'Rejected by a person' if approval == 'denied' else 'Approval expired'
    elif decision == 'allow':
        permission = 'Allowed by the configured policy'
    elif decision == 'deny':
        permission = 'Blocked by the configured policy'
    elif decision == 'prepare':
        permission = 'Prepared only; execution not permitted'
    else:
        permission = 'Approval evidence not recorded'
    status = row.get('task_status') or row['result']
    if row.get('receipt_state') in ('unknown', 'dispatched'):
        status = 'Outcome needs verification'
    else:
        status = {'completed': 'Completed', 'waiting_approval': 'Waiting for approval', 'running': 'In progress',
                  'failed': 'Failed', 'denied': 'Blocked', 'prepared': 'Prepared for review',
                  'verified': 'Verified', 'pending': 'Not started'}.get(status, 'Needs review')
    return {'id': row['id'], 'task_id': row['task_id'], 'run_id': row['run_id'],
            'timestamp': row['created_at'], 'agent': row.get('agent_name') or 'Workspace agent',
            'summary': TOOLS.get(row.get('tool'), 'Review an agent action'),
            'reason': (row.get('reason') or 'Reason not recorded')[:500],
            'policy_version': row.get('policy_version'), 'permission': permission, 'status': status,
            'environment': 'Vendor execution has not been qualified',
            'reversibility': 'Reversal not verified', 'can_reverse': False}


def install_trust_routes(app, current_user):
    @app.get('/api/trust')
    def trust(limit: int = Query(default=25, ge=1, le=100), user=Depends(current_user)):
        with scoped(user['tenant_id']) as conn:
            pending = conn.execute("SELECT count(*) AS n FROM approval_requests WHERE status='pending' AND expires_at>now()").fetchone()['n']
            rows = conn.execute('''WITH latest AS (
                SELECT DISTINCT ON(l.task_id) l.id,l.task_id,l.run_id,COALESCE(l.tool,t.tool) AS tool,l.created_at,l.result,
                    COALESCE(l.policy_version,t.policy_version::text) AS policy_version,
                    t.status AS task_status,t.decision,t.reason,g.name AS agent_name,
                    a.status AS approval_status,a.expires_at<=now() AS approval_expired,r.state AS receipt_state
                FROM audit_logs l JOIN agent_tasks t ON t.id=l.task_id AND t.tenant_id=l.tenant_id AND t.agent_id=l.agent_id
                JOIN agents g ON g.id=t.agent_id AND g.tenant_id=t.tenant_id
                LEFT JOIN approval_requests a ON a.id=t.approval_id AND a.tenant_id=t.tenant_id AND a.agent_id=t.agent_id
                LEFT JOIN tool_receipts r ON r.task_id=t.id AND r.tenant_id=t.tenant_id AND r.agent_id=t.agent_id
                ORDER BY l.task_id,l.created_at DESC,l.id DESC
            ) SELECT * FROM latest ORDER BY created_at DESC,id DESC LIMIT %s''', (limit,)).fetchall()
        return {'pending_approvals': pending, 'actions': [describe(r) for r in rows],
                'notice': 'Sandbox-tested; live business-vendor integrations are not verified.'}
