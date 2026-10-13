"""Founder-owned delivery checklist; no customer roles imply founder authority."""
from datetime import date as CalendarDate
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

STAGES = [
    ('Setup', [
        ('discovery', 'Discovery call completed', 'Confirm ticket volume, the current process and what good looks like.'),
        ('workspace', 'Workspace and owner account provisioned', 'Confirm the client can sign in.'),
        ('zendesk_oauth', 'Zendesk OAuth authorized and scopes confirmed', 'A registered account alone is not a connection.'),
        ('vendor_health', 'Vendor connection health checked', 'Confirm a real test read succeeds.')]),
    ('Configuration', [
        ('action_review', 'Applicable action types reviewed with the client', 'Explain replies, tags, escalations and refunds only where supported.'),
        ('automation_levels', 'Automation level set for each action type', 'Default to Always ask. This capability follows Gate 4.'),
        ('escalation_rules', 'Mandatory human escalation rules confirmed', 'Record the exceptions that always require a person.'),
        ('trust_walkthrough', 'Trust Dashboard walkthrough completed', 'Explain action evidence, approval requirements and reversal limitations.')]),
    ('First run', [
        ('first_live_run', 'First real agent run completed', 'Use live traffic, not a demonstration ticket.'),
        ('first_real_review', 'Client approved or rejected a real action', 'Demonstration approvals do not qualify.'),
        ('first_run_feedback', 'First-run friction and confusion recorded', 'Capture the feedback as a note, including what almost went wrong.')]),
    ('Handoff / steady state', [
        ('independent_dashboard', 'Client comfortable using the Trust Dashboard independently', 'Confirm the client no longer needs a guided walkthrough.'),
        ('week_one_review', 'Automation levels revisited after the first week', 'Record whether trust earned changed any Always ask settings.'),
        ('case_study', 'Case study or testimonial conversation held', 'Record the conversation; this does not grant permission to publish.'),
        ('billing', 'Pricing and billing conversation finalized', 'Record the agreed commercial position, including beta pricing.')]),
]
ITEMS = {key for _, items in STAGES for key, _, _ in items}
BLOCKED = {
    'automation_levels': 'Per-action settings are deferred until after Gate 4.',
    'week_one_review': 'Per-action settings are deferred until after Gate 4.',
    'first_live_run': 'Controlled live Zendesk actions are not qualified yet.',
    'first_real_review': 'Controlled live Zendesk actions are not qualified yet.',
}


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['not_started', 'in_progress', 'done'] = 'not_started'
    date: CalendarDate | None = None
    note: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def dated(self):
        if self.status != 'not_started' and self.date is None:
            raise ValueError('Add a date for an item that is in progress or done')
        return self


class ChecklistInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0)
    items: dict[str, ChecklistItem]
    notes: str = Field(default='', max_length=12000)

    @model_validator(mode='after')
    def known_items(self):
        if set(self.items) - ITEMS:
            raise ValueError('Unknown onboarding item')
        for key, value in self.items.items():
            if key in BLOCKED and value.status == 'done':
                raise ValueError(BLOCKED[key])
            if key == 'first_run_feedback' and value.status == 'done' and not value.note.strip():
                raise ValueError('Capture first-run feedback in the item note')
        return self
