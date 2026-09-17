"""Acme tool backend. Integer minor units only; no card data or external writes."""
from uuid import uuid4
import hashlib
from datetime import datetime, timezone, timedelta
from psycopg.types.json import Jsonb


def records(conn, kind):
    return [{**row['data'],'id':row['id']} for row in conn.execute('SELECT id,data FROM sim_records WHERE kind=%s ORDER BY id',(kind,)).fetchall()]


def get(conn, kind, identity, *, lock=False):
    query = 'SELECT data FROM sim_records WHERE kind=%s AND id=%s' + (' FOR UPDATE' if lock else '')
    row = conn.execute(query,(kind,identity)).fetchone()
    if not row:
        raise ValueError(f'{kind} was not found in this agent scope')
    return {**row['data'],'id':identity}


def put(conn, tenant, agent, kind, identity, data):
    data = {k:v for k,v in data.items() if k != 'id'}
    conn.execute('''INSERT INTO sim_records(tenant_id,agent_id,kind,id,data) VALUES(%s,%s,%s,%s,%s)
      ON CONFLICT(tenant_id,agent_id,kind,id) DO UPDATE SET data=EXCLUDED.data''',
      (tenant,agent,kind,identity,Jsonb(data)))


def investigate(conn, args, abuse_window_days=30):
    customer = get(conn,'customer',args['customer_id'])
    order = get(conn,'order',args['order_id'])
    if order['customer_id'] != customer['id']:
        raise ValueError('Customer does not own this order')
    payments = [p for p in records(conn,'payment') if p['order_id'] == order['id']]
    captured = [p for p in payments if p['status']=='captured']
    tickets = [t for t in records(conn,'ticket') if t['order_id']==order['id'] and t['customer_id']==customer['id']]
    duplicate = next((p for p in captured[1:] if p['amount_minor']==captured[0]['amount_minor'] and p['currency']==captured[0]['currency']),None)
    cutoff=datetime.now(timezone.utc)-timedelta(days=abuse_window_days)
    def recent(value):
        if not value:return False
        try:return datetime.fromisoformat(value)>=cutoff
        except (TypeError,ValueError):return False
    previous_claims = sum(1 for t in records(conn,'ticket') if t['customer_id']==customer['id'] and
                          t.get('category') in {'duplicate_charge','undelivered_order'} and recent(t.get('created_at')))
    historical_claims=sum(1 for c in customer.get('refund_claim_history',[]) if recent(c.get('created_at')))
    return {'customer_id':customer['id'],'customer_name':customer['name'],'order_id':order['id'],
            'ticket_id':tickets[0]['id'] if tickets else None, 'payments':payments,
            'duplicate':duplicate,'claim_count':previous_claims+historical_claims,'abuse_window_days':abuse_window_days,
            'duplicate_verified':duplicate is not None}


def execute(conn, tenant, agent, tool, args, key):
    if tool == 'operations.inspect':
        orders = records(conn,'order')
        payments = records(conn,'payment')
        return {'orders_checked':len(orders), 'payment_failures':[p for p in payments if p['status']=='failed'],
                'delayed_shipments':[s for s in records(conn,'shipment') if s['status']=='delayed'],
                'inventory_shortages':[i for i in records(conn,'inventory') if i['available'] < i['reorder_point']],
                'customer_escalations':[t for t in records(conn,'ticket') if t['status']=='escalated']}
    if tool == 'support.investigate':
        return investigate(conn,args)
    if tool == 'payment.refund':
        # Entity lock serializes different run IDs for the same provider payment.
        payment = get(conn,'payment',args['payment_id'],lock=True)
        refund_id = 'REF-' + args['payment_id']
        old = conn.execute("SELECT data FROM sim_records WHERE kind='refund' AND id=%s",(refund_id,)).fetchone()
        if old:
            return {'refund_id':refund_id,**old['data'],'reconciled':True}
        evidence = investigate(conn,args)
        if not evidence['duplicate'] or evidence['duplicate']['id'] != payment['id']:
            raise ValueError('Duplicate charge is no longer verified')
        if payment['order_id'] != args['order_id'] or payment['amount_minor'] != args['amount_minor'] or payment['currency'] != args['currency']:
            raise ValueError('Refund arguments do not match the provider payment')
        result = {'payment_id':payment['id'],'order_id':args['order_id'],'customer_id':args['customer_id'],
                  'amount_minor':payment['amount_minor'],'currency':payment['currency'],'status':'succeeded',
                  'idempotency_key':key}
        put(conn,tenant,agent,'refund',refund_id,result)
        put(conn,tenant,agent,'payment',payment['id'],{**payment,'status':'refunded'})
        return {'refund_id':refund_id,**result}
    if tool == 'payment.retry':
        payment = get(conn,'payment',args['entity_id'],lock=True)
        if payment['status'] == 'captured':
            return {'payment_id':payment['id'],'status':'captured','reconciled':True}
        if not payment.get('retry_eligible') or payment.get('retry_count',0) != args['retry_count']:
            raise ValueError('Payment retry eligibility changed')
        payment.update(retry_count=payment.get('retry_count',0)+1,
                       status='captured' if payment.get('retry_will_succeed',True) else 'failed')
        put(conn,tenant,agent,'payment',payment['id'],payment)
        return {'payment_id':payment['id'],'status':payment['status']}
    if tool == 'shipment.follow_up':
        shipment = get(conn,'shipment',args['entity_id'],lock=True)
        shipment['follow_up_requested'] = True
        put(conn,tenant,agent,'shipment',shipment['id'],shipment)
        return {'shipment_id':shipment['id'],'follow_up_requested':True,'status':shipment['status']}
    if tool in {'task.create','operations.escalate'}:
        identity = 'TASK-' + hashlib.sha256((tool+':'+args['entity_id']).encode()).hexdigest()[:24]
        existing=conn.execute("SELECT data FROM sim_records WHERE kind='task' AND id=%s",(identity,)).fetchone()
        if existing:
            return {'task_id':identity,**existing['data'],'reconciled':True}
        result = {**args,'status':'open','escalated':tool=='operations.escalate','owner':'Operations manager'}
        put(conn,tenant,agent,'task',identity,result)
        return {'task_id':identity,**result}
    if tool == 'ticket.update':
        ticket = get(conn,'ticket',args['entity_id'],lock=True)
        ticket.update(status=args['status'],note=args['note'])
        put(conn,tenant,agent,'ticket',ticket['id'],ticket)
        return {'ticket_id':ticket['id'],'status':ticket['status']}
    if tool == 'support.notify':
        customer = get(conn,'customer',args['customer_id'])
        order = get(conn,'order',args['order_id'])
        if order['customer_id'] != customer['id']:
            raise ValueError('Message recipient does not own this order')
        identity = 'MSG-' + key[-24:]
        result = {**args,'channel':'simulation_outbox','status':'recorded'}
        put(conn,tenant,agent,'message',identity,result)
        return {'message_id':identity,**result}
    raise ValueError('Tool is not implemented')


def verify(conn, tool, result):
    """Read back provider state; a dispatch response alone is not verification."""
    if tool == 'payment.refund':
        refund = get(conn,'refund',result['refund_id'])
        payment = get(conn,'payment',result['payment_id'])
        return refund['status']=='succeeded' and payment['status']=='refunded' and refund['amount_minor']==result['amount_minor']
    if tool == 'payment.retry':
        return get(conn,'payment',result['payment_id'])['status']=='captured'
    if tool == 'shipment.follow_up':
        return get(conn,'shipment',result['shipment_id']).get('follow_up_requested') is True
    if tool in {'task.create','operations.escalate'}:
        return get(conn,'task',result['task_id'])['status']=='open'
    if tool == 'ticket.update':
        return get(conn,'ticket',result['ticket_id'])['status']==result['status']
    if tool == 'support.notify':
        return get(conn,'message',result['message_id'])['status']=='recorded'
    return True


def reconcile(conn, tool, args, key):
    """UNKNOWN always reads provider state BEFORE re-execution. None is absent,
    and only this transactional simulator can prove absence (no external call).
    A real adapter must distinguish ABSENT from UNKNOWN and never blindly retry.
    """
    if tool == 'payment.refund':
        row = conn.execute("SELECT data FROM sim_records WHERE kind='refund' AND id=%s",('REF-'+args['payment_id'],)).fetchone()
        return {'refund_id':'REF-'+args['payment_id'],**row['data']} if row else None
    if tool in {'task.create','operations.escalate','support.notify'}:
        kind = 'message' if tool=='support.notify' else 'task'
        identity = 'MSG-'+key[-24:] if kind=='message' else 'TASK-'+hashlib.sha256((tool+':'+args['entity_id']).encode()).hexdigest()[:24]
        row = conn.execute('SELECT data FROM sim_records WHERE kind=%s AND id=%s',(kind,identity)).fetchone()
        return {kind+'_id':identity,**row['data']} if row else None
    # Updates are idempotent assignments but the runtime still checks a receipt.
    return None
