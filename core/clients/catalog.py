"""Client workspaces use explicitly isolated simulation until real adapters qualify."""
CATEGORIES = {
    'ecommerce': {'name': 'Ecommerce', 'provider': 'sandbox_commerce', 'description': 'Orders, payment investigation and shipment exceptions using fictional records.', 'capabilities': ['Order lookup','Refund investigation','Shipment follow-up']},
    'custom': {'name': 'Custom website or app', 'provider': 'sandbox_custom', 'description': 'Scoped backend API access and operational tasks using fictional records.', 'capabilities': ['Submit work through API','Poll verified outcomes','Operational tasks']},
    'crm': {'name': 'CRM and helpdesk', 'provider': 'sandbox_helpdesk', 'description': 'Ticket updates, escalation and simulated customer replies.', 'capabilities': ['Ticket resolution','Escalations','Unsent reply outbox']},
}


def profile(conn):
    return conn.execute("SELECT * FROM workspace_profiles WHERE tenant_id::text=current_setting('app.tenant_id',true)").fetchone()


def tool_category(name):
    if name.startswith(('ticket.', 'support.notify')): return 'crm'
    if name.startswith(('task.', 'operations.escalate')): return 'custom'
    return 'ecommerce'


def require_connected(conn, category):
    row=conn.execute('SELECT * FROM workspace_connections WHERE category=%s', (category,)).fetchone()
    if not row or row['status']!='connected':
        raise ValueError('Connect the '+CATEGORIES[category]['name']+' sandbox in Integrations first')
    return row
