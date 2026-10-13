import pytest
from pydantic import ValidationError
from apps.api.trust_routes import describe
from core.clients.onboarding import ChecklistInput, ITEMS, BLOCKED


def row(**overrides):
    return {'id':1,'task_id':'task','run_id':'run','created_at':'2026-09-26T00:00:00Z',
            'tool':'payment.refund','result':'completed','task_status':'completed', **overrides}


def test_trust_does_not_invent_approval_or_reversal():
    value = describe(row())
    assert value['reason'] == 'Reason not recorded'
    assert value['permission'] == 'Approval evidence not recorded'
    assert value['can_reverse'] is False and value['reversibility'] == 'Reversal not verified'


def test_uncertain_receipt_overrides_completed_task():
    value = describe(row(receipt_state='unknown', decision='allow'))
    assert value['status'] == 'Outcome needs verification'
    assert value['permission'] == 'Allowed by the configured policy'


def test_human_approval_and_expiry_are_distinct():
    assert describe(row(approval_status='approved'))['permission'] == 'Approved by a person'
    assert describe(row(approval_status='pending',approval_expired=True))['permission'] == 'Approval expired'


def test_checklist_has_all_fifteen_items():
    assert len(ITEMS) == 15
    value = ChecklistInput(version=0,items={'discovery':{'status':'done','date':'2026-09-26','note':'Needs faster triage.'}},notes='Founder-only notes')
    assert value.items['discovery'].status == 'done'


@pytest.mark.parametrize('key', list(BLOCKED))
def test_unqualified_milestones_cannot_be_marked_done(key):
    with pytest.raises(ValidationError):
        ChecklistInput(version=0,items={key:{'status':'done','date':'2026-09-26'}})


@pytest.mark.parametrize('items', [{'unknown':{}}, {'discovery':{'status':'done'}},
                                 {'first_run_feedback':{'status':'done','date':'2026-09-26','note':''}}])
def test_checklist_requires_known_items_dates_and_feedback(items):
    with pytest.raises(ValidationError):
        ChecklistInput(version=0,items=items)
