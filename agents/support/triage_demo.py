"""Fictional Zendesk-style payment complaint triage, without refunds or replies."""


def build_actions(context, rules):
    if not context.get('ticket_id'):
        raise ValueError('A linked demo ticket is required for triage')
    return [('ticket.update', {'entity_id': context['ticket_id'], 'status': 'escalated',
            'note': 'Payment complaint reviewed in the simulation. A person must decide the resolution. No refund or customer reply has been sent.'})]
