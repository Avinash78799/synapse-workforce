"""The only entry point for tools: validate -> authorize -> dispatch -> verify.

Each dispatch is durable before a mutation. Simulator mutation + receipt commit
atomically. A lost response is reconciled from receipts/provider state, never
replayed with a new idempotency key.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from core.db import scoped
from core.policies.engine import PolicyRules, evaluate
from core.runtime.audit import audit
from core.tools.registry import TOOLS
from core.connectors import get_connector


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def execute_task(tenant, agent_id, task_id):
    with scoped(tenant,agent_id) as conn:
        task = conn.execute('SELECT * FROM agent_tasks WHERE id=%s FOR UPDATE',(task_id,)).fetchone()
        if task['status'] in {'completed','denied','prepared','failed','expired','blocked'}:
            return task
        agent = conn.execute('SELECT * FROM agents WHERE id=%s FOR UPDATE',(agent_id,)).fetchone()
        run = conn.execute('SELECT * FROM agent_runs WHERE id=%s',(task['run_id'],)).fetchone()
        tool = TOOLS.get(task['tool'])
        if not tool or tool.agent != agent['kind']:
            raise PermissionError('Tool is outside this agent registry')
        args = tool.arguments.model_validate(task['arguments']).model_dump()
        fingerprint = digest({'tool':tool.name,'args':args})
        receipt = conn.execute('SELECT * FROM tool_receipts WHERE idempotency_key=%s',(task['idempotency_key'],)).fetchone()
        if receipt and receipt['arguments_digest'] != fingerprint:
            raise PermissionError('Idempotency key was reused with different arguments')
        # Graceful drain permits a dispatched call to finish/reconcile, but no
        # new dispatch. Hard abort blocks both and leaves a reconciliation flag.
        if agent['execution_epoch']!=run['execution_epoch'] or agent['status'] == 'hard_aborted' or (agent['status'] != 'active' and not receipt):
            conn.execute("UPDATE agent_runs SET status=%s WHERE id=%s",('aborted' if agent['status']=='hard_aborted' else 'paused',run['id']))
            return {**task,'status':'paused'}
        if receipt and receipt['state']=='unknown':
            conn.execute("UPDATE agent_tasks SET status='failed',verification='unknown',reason='Unknown provider state; human reconciliation required' WHERE id=%s",(task_id,))
            audit(conn,tenant,agent_id,'reconciliation.required','unknown',run=run['id'],task=task_id,
                  tool=tool.name,policy=task['policy_version'])
            return {**task,'status':'failed'}
        policy = conn.execute('SELECT rules FROM agent_policies WHERE version=%s',(task['policy_version'],)).fetchone()
        rules = PolicyRules.model_validate(policy['rules'])
        permission = conn.execute('SELECT allowed FROM agent_permissions WHERE action=%s',(tool.name,)).fetchone()
        enabled = conn.execute('SELECT enabled FROM agent_tools WHERE name=%s',(tool.name,)).fetchone()
        skill = conn.execute('SELECT enabled,autonomy_cap FROM agent_skills WHERE name=%s',(tool.skill,)).fetchone()
        required_skills = []
        if tool.name=='operations.inspect':
            required_skills=['InventorySkill','ShipmentSkill','PaymentSkill']
        if tool.name=='support.investigate':
            required_skills=['CustomerLookupSkill','OrderLookupSkill']
        if tool.name=='task.create' and args['entity_id'].startswith('INV-'):
            required_skills=['InventorySkill']
        dependencies_enabled=all((conn.execute('SELECT enabled FROM agent_skills WHERE name=%s',(s,)).fetchone() or {}).get('enabled',False) for s in required_skills)
        allowed = bool(permission and permission['allowed'] and enabled and enabled['enabled'])
        # Abuse protection cannot be disabled to enable automatic refunds.
        if tool.name == 'payment.refund':
            abuse = conn.execute("SELECT enabled FROM agent_skills WHERE name='AbuseSignalSkill'").fetchone()
            allowed = allowed and bool(abuse and abuse['enabled'])
            # Fresh trusted evidence replaces any stale/model-supplied fraud signal.
            evidence = get_connector(tool.name).investigate(conn,args,rules.abuse_window_days)
            args['claim_count'] = evidence['claim_count']
        caps = [agent['autonomy']]
        # Overrides can restrict an agent, never grant authority above it.
        for name in [tool.skill]+required_skills+(['AbuseSignalSkill'] if tool.name=='payment.refund' else []):
            row=conn.execute('SELECT autonomy_cap FROM agent_skills WHERE name=%s',(name,)).fetchone()
            if row and row['autonomy_cap'] is not None:
                caps.append(row['autonomy_cap'])
        decision = evaluate(tool.name,args,rules,allowed=allowed,
                            skill_enabled=bool(skill and skill['enabled'] and dependencies_enabled),autonomy=min(caps),mutates=tool.mutates)
        approval_id = task['approval_id']
        if not receipt:
            if decision.outcome == 'require_approval':
                approval = conn.execute('SELECT * FROM approval_requests WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
                if not approval:
                    approval_id = uuid4()
                    expires = datetime.now(timezone.utc)+timedelta(minutes=rules.approval_ttl_minutes)
                    conn.execute('''INSERT INTO approval_requests(id,tenant_id,agent_id,run_id,task_id,policy_version,
                        action_digest,required_role,reason,expires_at,independent_required) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (approval_id,tenant,agent_id,run['id'],task_id,task['policy_version'],fingerprint,rules.approval_role,decision.reason,expires,
                         tool.name=='payment.refund' and args['amount_minor']>=rules.independent_approval_threshold_minor))
                    conn.execute('INSERT INTO queue_outbox(tenant_id,agent_id,run_id,available_at) VALUES(%s,%s,%s,%s)',(tenant,agent_id,run['id'],expires))
                    audit(conn,tenant,agent_id,'approval.requested','pending',run=run['id'],task=task_id,
                          tool=tool.name,policy=task['policy_version'],approval=approval_id,details={'reason':decision.reason})
                    conn.execute("UPDATE agent_tasks SET status='waiting_approval',decision=%s,reason=%s,approval_id=%s WHERE id=%s",
                                 (decision.outcome,decision.reason,approval_id,task_id))
                    return {**task,'status':'waiting_approval'}
                approval_id = approval['id']
                if approval['status']=='pending' and approval['expires_at']<=datetime.now(timezone.utc):
                    conn.execute("UPDATE approval_requests SET status='expired',decided_at=now(),decision_reason='Default deny on timeout' WHERE id=%s",(approval_id,))
                    approval['status']='expired'
                    audit(conn,tenant,agent_id,'approval.expired','denied',run=run['id'],task=task_id,
                          tool=tool.name,policy=task['policy_version'],approval=approval_id)
                if approval['status']=='pending':
                    return {**task,'status':'waiting_approval'}
                if approval['status']!='approved' or approval['action_digest']!=fingerprint or (
                    approval['independent_required'] and approval['decided_by']==run['actor_id']):
                    conn.execute("UPDATE agent_tasks SET status='denied',reason='Approval denied, expired, or action changed' WHERE id=%s",(task_id,))
                    return {**task,'status':'denied'}
            elif decision.outcome in {'deny','prepare'}:
                status = 'denied' if decision.outcome=='deny' else 'prepared'
                conn.execute('UPDATE agent_tasks SET status=%s,decision=%s,reason=%s,finished_at=now() WHERE id=%s',
                             (status,decision.outcome,decision.reason,task_id))
                audit(conn,tenant,agent_id,'tool.policy',status,run=run['id'],task=task_id,tool=tool.name,
                      policy=task['policy_version'],details={'reason':decision.reason})
                return {**task,'status':status}
            conn.execute('''INSERT INTO tool_receipts(tenant_id,agent_id,idempotency_key,task_id,tool,arguments_digest,state)
                VALUES(%s,%s,%s,%s,%s,%s,'dispatched')''',(tenant,agent_id,task['idempotency_key'],task_id,tool.name,fingerprint))
            audit(conn,tenant,agent_id,'tool.dispatched','dispatched',actor=run['actor_id'],run=run['id'],task=task_id,
                  tool=tool.name,policy=task['policy_version'],approval=approval_id,version=run['agent_version'])
            conn.execute("UPDATE agent_tasks SET status='running',decision=%s,reason=%s,attempts=attempts+1 WHERE id=%s",
                         (decision.outcome,decision.reason,task_id))
    # This transaction represents the provider call. No database locks are held
    # across a real network call; V1's provider is a transactional simulator.
    try:
        with scoped(tenant,agent_id) as conn:
            current = conn.execute('SELECT status FROM agents WHERE id=%s',(agent_id,)).fetchone()
            if current['status']=='hard_aborted':
                conn.execute("UPDATE tool_receipts SET state='unknown',updated_at=now() WHERE idempotency_key=%s",(task['idempotency_key'],))
                conn.execute("UPDATE agent_runs SET status='aborted' WHERE id=%s",(run['id'],))
                return {**task,'status':'paused'}
            previous = conn.execute('SELECT * FROM tool_receipts WHERE idempotency_key=%s FOR UPDATE',(task['idempotency_key'],)).fetchone()
            connector = get_connector(tool.name)
            if previous['state']=='confirmed':
                result = previous['result']
            else:
                result = connector.reconcile(conn,tool.name,args,task['idempotency_key']) if tool.mutates else None
                if result is None:
                    result = connector.execute(conn,tenant,agent_id,tool.name,args,task['idempotency_key'])
                conn.execute("UPDATE tool_receipts SET state='confirmed',result=%s,updated_at=now() WHERE idempotency_key=%s",
                             (Jsonb(result),task['idempotency_key']))
        with scoped(tenant,agent_id) as conn:
            verified = get_connector(tool.name).verify(conn,tool.name,result)
            status = 'completed' if verified else 'failed'
            conn.execute('UPDATE agent_tasks SET status=%s,verification=%s,result=%s,finished_at=now() WHERE id=%s',
                         (status,'passed' if verified else 'failed',Jsonb(result),task_id))
            audit(conn,tenant,agent_id,'tool.verified',status,run=run['id'],task=task_id,tool=tool.name,
                  policy=task['policy_version'],approval=approval_id,details={'verification':'passed' if verified else 'failed'})
            return {**task,'status':status,'result':result}
    except ValueError as exc:
        with scoped(tenant,agent_id) as conn:
            conn.execute("UPDATE agent_tasks SET status='failed',reason=%s,verification='failed',finished_at=now() WHERE id=%s",(str(exc),task_id))
            conn.execute("UPDATE tool_receipts SET state='failed',updated_at=now() WHERE idempotency_key=%s",(task['idempotency_key'],))
            audit(conn,tenant,agent_id,'tool.failed','failed',run=run['id'],task=task_id,tool=tool.name,
                  policy=task['policy_version'],details={'reason':str(exc)})
        return {**task,'status':'failed'}
