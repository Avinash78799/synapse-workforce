def build_actions(context, rules):
    actions = []
    for p in context['payment_failures']:
        if p.get('retry_eligible') and p.get('retry_count',0)<rules.max_payment_retries:
            actions.append(('payment.retry',{'entity_id':p['id'],'retry_eligible':True,'retry_count':p.get('retry_count',0)}))
        else:
            actions.append(('operations.escalate',{'entity_id':p['id'],'title':'Investigate failed payment '+p['id'],'priority':'high'}))
    for s in context['delayed_shipments']:
        actions.append(('shipment.follow_up',{'entity_id':s['id']}))
    for i in context['inventory_shortages']:
        actions.append(('task.create',{'entity_id':i['id'],'title':'Replenish '+i['product_name'],'priority':'normal'}))
    for ticket in context['customer_escalations']:
        actions.append(('operations.escalate',{'entity_id':ticket['id'],'title':'Review customer escalation '+ticket['id'],'priority':'high'}))
    return actions
