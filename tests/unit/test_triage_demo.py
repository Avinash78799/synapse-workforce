import pytest
from agents.support.triage_demo import build_actions


@pytest.mark.parametrize('duplicate',[None,{'id':'PAY-1','amount_minor':100000}])
def test_triage_never_refunds_or_sends_customer_messages(duplicate):
    actions=build_actions({'ticket_id':'TKT-001','duplicate':duplicate},None)
    assert [name for name,_ in actions]==['ticket.update']
    assert actions[0][1]['status']=='escalated'
    assert actions[0][1]['entity_id']=='TKT-001'


def test_triage_requires_linked_ticket():
    with pytest.raises(ValueError):build_actions({},None)
