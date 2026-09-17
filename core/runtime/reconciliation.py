"""Operator-assisted read-back only. Never dispatch a financial action here."""
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from core.runtime.audit import audit
from core.runtime.executor import digest
from core.tools.registry import TOOLS
from core.connectors import get_connector


def resolve(conn,user,task_id,choice,reason):
    task=conn.execute('SELECT * FROM agent_tasks WHERE id=%s',(task_id,)).fetchone()
    if not task:
        raise HTTPException(404,'Action not found')
    locked=conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,1)) AS ok',(str(task['run_id']),)).fetchone()['ok']
    if not locked:
        raise HTTPException(409,'The worker is still processing this run')
    receipt=conn.execute('SELECT * FROM tool_receipts WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
    if not receipt or receipt['state']!='unknown':
        raise HTTPException(409,'This action is not awaiting reconciliation')
    if choice=='close_unresolved':
        status='failed'
        result=None
        verification='unresolved_closed'
        conn.execute("UPDATE tool_receipts SET state='failed',updated_at=now() WHERE task_id=%s",(task_id,))
    else:
        tool=TOOLS[task['tool']]
        args=tool.arguments.model_validate(task['arguments']).model_dump()
        if digest({'tool':tool.name,'args':args})!=receipt['arguments_digest']:
            raise HTTPException(409,'Action evidence does not match its original arguments')
        # Control-plane scope can see all tenant agents. Narrow it before reading
        # business records, otherwise matching order IDs in another agent could
        # appear to prove this action succeeded.
        conn.execute("SELECT set_config('app.agent_id',%s,true)",(str(task['agent_id']),))
        connector = get_connector(tool.name)
        result=connector.reconcile(conn,tool.name,args,task['idempotency_key']) or receipt['result']
        if not result:
            raise HTTPException(409,'No conclusive provider evidence. Keep frozen or close unresolved; no retry was issued')
        if tool.name=='payment.refund' and any(result.get(k)!=args.get(k) for k in
                ('customer_id','order_id','payment_id','amount_minor','currency')):
            raise HTTPException(409,'Refund evidence does not match this action')
        try:
            verified=connector.verify(conn,tool.name,result)
        except (ValueError,KeyError):
            verified=False
        if not verified:
            raise HTTPException(409,'Provider state has not verified success; the action remains frozen')
        status='completed'
        verification='passed'
        conn.execute("UPDATE tool_receipts SET state='confirmed',result=%s,updated_at=now() WHERE task_id=%s",(Jsonb(result),task_id))
    conn.execute('UPDATE agent_tasks SET status=%s,verification=%s,result=%s,reason=%s,finished_at=now() WHERE id=%s',
                 (status,verification,Jsonb(result),reason,task_id))
    # Human reconciliation closes this run for review. It does not send a
    # customer message, unfreeze other actions, clear an abort, or requeue work.
    conn.execute("UPDATE agent_tasks SET status='blocked',reason='Human reconciliation closed the run for review' WHERE run_id=%s AND status IN ('pending','waiting_approval')",(task['run_id'],))
    conn.execute("UPDATE approval_requests SET status='denied',decision_reason='Run closed during reconciliation',decided_by=%s,decided_at=now() WHERE run_id=%s AND status='pending'",(user['id'],task['run_id']))
    conn.execute("UPDATE agent_runs SET status=CASE WHEN status='aborted' THEN status ELSE 'escalated' END,error='Reconciliation reviewed; follow-up requires human review',finished_at=now() WHERE id=%s",(task['run_id'],))
    audit(conn,user['tenant_id'],task['agent_id'],'reconciliation.reviewed',verification,actor=user['id'],
          run=task['run_id'],task=task_id,tool=task['tool'],policy=task['policy_version'],
          details={'choice':choice,'reason':reason,'no_new_dispatch':True})
    return {'status':verification,'run_id':task['run_id'],'resumed':False}
