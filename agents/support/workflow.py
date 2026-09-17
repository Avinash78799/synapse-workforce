def build_actions(context, rules):
    actions = []
    duplicate = context['duplicate']
    if duplicate:
        actions.append(('payment.refund',{'customer_id':context['customer_id'],'order_id':context['order_id'],
            'payment_id':duplicate['id'],'amount_minor':duplicate['amount_minor'],'currency':duplicate['currency'],
            'duplicate_verified':True,'claim_count':context['claim_count']}))
        if context['ticket_id']:
            actions.append(('ticket.update',{'entity_id':context['ticket_id'],'status':'resolved',
                            'note':'Duplicate charge refund was verified in the payment system.'}))
        text = 'Your duplicate-charge refund has been verified. Your original order remains active.'
    else:
        if context['ticket_id']:
            actions.append(('ticket.update',{'entity_id':context['ticket_id'],'status':'escalated',
                            'note':'No eligible duplicate charge was found. Human investigation is required.'}))
        text = 'We could not verify an eligible duplicate charge. Your case has been referred for review.'
    actions.append(('support.notify',{'customer_id':context['customer_id'],'order_id':context['order_id'],'text':text}))
    return actions
