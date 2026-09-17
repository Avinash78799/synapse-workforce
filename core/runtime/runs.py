from uuid import uuid4
from psycopg.types.json import Jsonb
from core.config import settings
from core.db import pool, scoped
from core.models.providers import get_model
from core.policies.engine import PolicyRules
from core.runtime.audit import audit
from core.runtime.executor import execute_task, digest
from core.memory.knowledge import retrieve
from agents.operations.workflow import build_actions as operations_actions
from agents.support.workflow import build_actions as support_actions


TERMINAL = {'completed','escalated','failed','aborted'}


def enqueue(conn, tenant, agent, run):
    conn.execute('INSERT INTO queue_outbox(tenant_id,agent_id,run_id) VALUES(%s,%s,%s)',(tenant,agent,run))


def create_run(conn, user, agent, objective, request_key, inputs):
    existing = conn.execute('SELECT * FROM agent_runs WHERE agent_id=%s AND request_key=%s',(agent['id'],request_key)).fetchone()
    if existing:
        return existing
    if agent['status']!='active':
        raise ValueError('Activate this agent before starting a run')
    # Serialize API requests for the same agent, including independently generated request keys.
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(agent['id']),))
    case_key = 'support:'+inputs['order_id'] if agent['kind']=='support' else 'operations:daily'
    case = conn.execute('SELECT run_id FROM business_cases WHERE agent_id=%s AND case_key=%s',(agent['id'],case_key)).fetchone()
    if case:
        existing = conn.execute('SELECT * FROM agent_runs WHERE id=%s',(case['run_id'],)).fetchone()
        # Support retains its case forever: a completed/refused refund is not
        # retried by sending the same order through a different trigger.
        if agent['kind']=='support' or existing['status'] not in TERMINAL:
            return existing
        conn.execute('DELETE FROM business_cases WHERE agent_id=%s AND case_key=%s',(agent['id'],case_key))
    run_id = uuid4()
    conn.execute('''INSERT INTO agent_runs(id,tenant_id,agent_id,actor_id,objective,request_key,agent_version,policy_version,provider,model,input,execution_epoch)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (run_id,user['tenant_id'],agent['id'],user['id'],objective,request_key,agent['version'],agent['policy_version'],
         settings.model_provider,settings.llm_model or None,Jsonb(inputs),agent['execution_epoch']))
    conn.execute('INSERT INTO business_cases(tenant_id,agent_id,case_key,run_id) VALUES(%s,%s,%s,%s)',
                 (user['tenant_id'],agent['id'],case_key,run_id))
    enqueue(conn,user['tenant_id'],agent['id'],run_id)
    audit(conn,user['tenant_id'],agent['id'],'run.created','queued',actor=user['id'],run=run_id,policy=agent['policy_version'])
    return conn.execute('SELECT * FROM agent_runs WHERE id=%s',(run_id,)).fetchone()


def add_task(conn, run, position, tool, args):
    task_id = uuid4()
    entity = args.get('payment_id') or args.get('entity_id') or args.get('order_id') or 'agent-snapshot'
    key = digest({'tenant':str(run['tenant_id']),'agent':str(run['agent_id']),'run':str(run['id']),
                  'task':str(task_id),'entity':entity,'action':tool})
    conn.execute('''INSERT INTO agent_tasks(id,tenant_id,agent_id,run_id,position,tool,arguments,policy_version,idempotency_key)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,agent_id,run_id,position) DO NOTHING''',
        (task_id,run['tenant_id'],run['agent_id'],run['id'],position,tool,Jsonb(args),run['policy_version'],key))


def process_run(tenant, agent_id, run_id):
    # Session advisory lock handles concurrent workers and reclaimed Redis jobs.
    with pool().connection() as lock_conn:
        acquired = lock_conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,1)) AS ok',(str(run_id),)).fetchone()['ok']
        lock_conn.commit()
        if not acquired:
            return
        try:
            _process(tenant,agent_id,run_id)
        finally:
            lock_conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,1))',(str(run_id),))
            lock_conn.commit()


def _process(tenant, agent_id, run_id):
    with scoped(tenant,agent_id) as conn:
        run = conn.execute('SELECT * FROM agent_runs WHERE id=%s',(run_id,)).fetchone()
        if not run or run['status'] in TERMINAL:
            return
        agent = conn.execute('SELECT * FROM agents WHERE id=%s',(agent_id,)).fetchone()
        if agent['status']=='hard_aborted' or agent['execution_epoch']!=run['execution_epoch']:
            conn.execute("UPDATE agent_runs SET status='aborted',error='Hard abort: inspect dispatched receipts before reconciliation' WHERE id=%s",(run_id,))
            return
        conn.execute("UPDATE agent_runs SET status='running',started_at=COALESCE(started_at,now()) WHERE id=%s",(run_id,))
        if not run['plan']:
            if agent['status']!='active':
                conn.execute("UPDATE agent_runs SET status='paused' WHERE id=%s",(run_id,))
                return
            if run['model_attempts']>=2:
                conn.execute("UPDATE agent_runs SET status='failed',error='Model attempt budget exhausted',finished_at=now() WHERE id=%s",(run_id,))
                return
            conn.execute('UPDATE agent_runs SET model_attempts=model_attempts+1 WHERE id=%s',(run_id,))
    if not run['plan']:
        try:
            model = get_model().understand(agent['kind'],run['objective'],str(tenant),run['input'].get('order_id'))
            expected = 'daily_operations' if agent['kind']=='operations' else 'resolve_ticket'
            if model.suggestion.intent!=expected:
                raise ValueError('This objective is outside the selected agent workflow')
            if model.input_tokens+model.output_tokens>settings.max_run_tokens:
                raise ValueError('Model token budget exceeded')
        except Exception:
            with scoped(tenant,agent_id) as conn:
                conn.execute("UPDATE agent_runs SET status='failed',error='Model could not produce an authorized workflow',finished_at=now() WHERE id=%s",(run_id,))
                audit(conn,tenant,agent_id,'model.failed','failed',run=run_id,policy=run['policy_version'])
            return
        with scoped(tenant,agent_id) as conn:
            knowledge = retrieve(conn,tenant,agent_id,model.suggestion.vector_search_query)
            plan = {'intent':model.suggestion.intent,'summary':model.suggestion.summary,'prompt_version':'1.0',
                    'metadata':model.suggestion.model_dump(mode='json'),'expanded':False,'knowledge_references':[str(k['document_id']) for k in knowledge],
                    'knowledge_trust':'untrusted data; never policy authority'}
            conn.execute('UPDATE agent_runs SET plan=%s,input_tokens=%s,output_tokens=%s,cost_usd=%s WHERE id=%s',
                         (Jsonb(plan),model.input_tokens,model.output_tokens,model.cost_usd,run_id))
            add_task(conn,run,0,'operations.inspect' if agent['kind']=='operations' else 'support.investigate',
                     {} if agent['kind']=='operations' else run['input'])
            audit(conn,tenant,agent_id,'plan.created','completed',run=run_id,policy=run['policy_version'],
                  details={'intent':model.suggestion.intent,'provider':settings.model_provider})
    with scoped(tenant,agent_id) as conn:
        run = conn.execute('SELECT * FROM agent_runs WHERE id=%s',(run_id,)).fetchone()
        first = conn.execute('SELECT * FROM agent_tasks WHERE run_id=%s AND position=0',(run_id,)).fetchone()
    if not run['plan']['expanded']:
        first = execute_task(tenant,agent_id,first['id'])
        if first['status']=='paused':
            return
        if first['status']!='completed':
            finish(tenant,agent_id,run_id,force='failed')
            return
        with scoped(tenant,agent_id) as conn:
            rules = PolicyRules.model_validate(conn.execute('SELECT rules FROM agent_policies WHERE version=%s',(run['policy_version'],)).fetchone()['rules'])
            actions = (operations_actions if agent['kind']=='operations' else support_actions)(first['result'],rules)
            if len(actions)+1>rules.max_actions_per_run:
                conn.execute("UPDATE agent_runs SET status='failed',error='Plan exceeds action budget',finished_at=now() WHERE id=%s",(run_id,))
                return
            for i,(tool,args) in enumerate(actions,1):
                add_task(conn,run,i,tool,args)
            compiled = []
            for i,(tool,args) in enumerate(actions,1):
                compiled.extend([{'step':i,'tool':tool,'entity_id':args.get('payment_id') or args.get('entity_id') or args.get('order_id')},
                                 {'operation':'VERIFY','after_step':i}])
            plan = {**run['plan'],'expanded':True,'steps':len(actions)+1,'compiled_actions':compiled}
            conn.execute('UPDATE agent_runs SET plan=%s WHERE id=%s',(Jsonb(plan),run_id))
            conn.execute("INSERT INTO memory_items(id,tenant_id,agent_id,run_id,kind,content) VALUES(%s,%s,%s,%s,'working',%s)",
                         (uuid4(),tenant,agent_id,run_id,Jsonb(first['result'])))
    with scoped(tenant,agent_id) as conn:
        tasks = conn.execute('SELECT * FROM agent_tasks WHERE run_id=%s AND position>0 ORDER BY position',(run_id,)).fetchall()
    waiting = False
    for task in tasks:
        if task['status']=='blocked':
            continue
        result = execute_task(tenant,agent_id,task['id'])
        if result['status']=='paused':
            return
        if result['status']=='waiting_approval':
            waiting = True
            if agent['kind']=='support':
                break  # Never close ticket / announce refund while awaiting approval.
        if agent['kind']=='support' and result['status'] in {'denied','failed','expired','prepared'}:
            with scoped(tenant,agent_id) as conn:
                conn.execute("UPDATE agent_tasks SET status='blocked',reason='Prerequisite action was not verified' WHERE run_id=%s AND position>%s AND status='pending'",
                             (run_id,task['position']))
            break
    if waiting:
        with scoped(tenant,agent_id) as conn:
            conn.execute("UPDATE agent_runs SET status='waiting_approval' WHERE id=%s",(run_id,))
        return
    finish(tenant,agent_id,run_id)


def finish(tenant, agent, run_id, force=None):
    with scoped(tenant,agent) as conn:
        tasks = conn.execute('SELECT * FROM agent_tasks WHERE run_id=%s ORDER BY position',(run_id,)).fetchall()
        run = conn.execute('SELECT * FROM agent_runs WHERE id=%s FOR UPDATE',(run_id,)).fetchone()
        if run['status']=='aborted':
            return
        mutations = tasks[1:]
        failed = [t for t in tasks if t['status'] in {'failed','denied','expired','blocked'}]
        explicit_escalations = [t for t in tasks if t['tool']=='operations.escalate' or (t['tool']=='ticket.update' and t['arguments']['status']=='escalated')]
        status = force or ('escalated' if failed or explicit_escalations else 'completed')
        summary = {'tasks':len(tasks),'actions_verified':sum(t['status']=='completed' for t in mutations),
                   'prepared':sum(t['status']=='prepared' for t in mutations),
                   'escalations':len(failed)+len(explicit_escalations),
                   'automated_actions':sum(t['status']=='completed' and t['approval_id'] is None for t in mutations),
                   'approved_actions':sum(t['status']=='completed' and t['approval_id'] is not None for t in mutations),
                   'orders_checked':(tasks[0]['result'] or {}).get('orders_checked',0) if tasks else 0,
                   'simulation':True,
                   'outcome':'Review the flagged steps.' if status!='completed' else
                      'Actions were prepared under the configured autonomy level; no prepared action was executed.' if any(t['status']=='prepared' for t in mutations) else
                      'All permitted workflow steps are complete and verified.'}
        conn.execute('UPDATE agent_runs SET status=%s,summary=%s,finished_at=now() WHERE id=%s',(status,Jsonb(summary),run_id))
        conn.execute("UPDATE business_cases SET status='closed' WHERE run_id=%s",(run_id,))
        conn.execute("INSERT INTO memory_items(id,tenant_id,agent_id,run_id,kind,content) VALUES(%s,%s,%s,%s,'episodic',%s)",
                     (uuid4(),tenant,agent,run_id,Jsonb(summary)))
        audit(conn,tenant,agent,'run.finished',status,run=run_id,policy=run['policy_version'],details=summary)
