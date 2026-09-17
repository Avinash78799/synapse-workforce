"""Deterministic authorization. Model output is never an authorization input."""
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field


class PolicyRules(BaseModel):
    model_config = ConfigDict(extra='forbid')
    auto_refund_limit_minor: int = Field(default=500_000, ge=0, le=100_000_000)
    independent_approval_threshold_minor: int = Field(default=500_000, ge=0, le=100_000_000)
    currency: str = 'INR'
    abuse_claim_threshold: int = Field(default=3, ge=2, le=100)
    abuse_window_days: int = Field(default=30, ge=1, le=365)
    approval_ttl_minutes: int = Field(default=60, ge=1, le=1440)
    approval_role: str = 'finance_approver'
    max_payment_retries: int = Field(default=2, ge=0, le=3)
    max_actions_per_run: int = Field(default=100, ge=1, le=100)


@dataclass(frozen=True)
class Decision:
    outcome: str
    reason: str


def evaluate(tool, args, rules, *, allowed, skill_enabled, autonomy=4, mutates=False, consent_granted=True):
    if not consent_granted:
        return Decision('deny', 'Action rejected: Data Principal has not granted or has withdrawn consent under DPDP Act')
    if not allowed or not skill_enabled:
        return Decision('deny', 'Tool permission or required skill is disabled')
    if tool in {'customer.delete', 'account.change'}:
        return Decision('deny', 'This action is prohibited')
    if not mutates:
        return Decision('allow', 'Read-only tool is authorized')
    if autonomy <= 2:
        return Decision('prepare', 'Autonomy level permits observation, recommendations, or preparation only')
    if tool == 'payment.refund':
        if args.get('currency') != rules.currency:
            return Decision('deny', 'Refund currency is outside the configured policy')
        if args.get('amount_minor',0) <= 0 or not args.get('duplicate_verified'):
            return Decision('deny', 'A positive, verified duplicate charge is required')
        if args['amount_minor'] >= rules.independent_approval_threshold_minor:
            return Decision('require_approval', 'Amount requires an independent financial approver')
        if args.get('claim_count',0) >= rules.abuse_claim_threshold:
            return Decision('require_approval', 'Repeated refund claims require human review')
        if args['amount_minor'] > rules.auto_refund_limit_minor:
            return Decision('require_approval', 'Amount exceeds the automatic refund limit')
    if tool == 'payment.retry' and (not args.get('retry_eligible') or args.get('retry_count',0) >= rules.max_payment_retries):
        return Decision('deny', 'Payment is not eligible for another retry')
    if autonomy == 3:
        return Decision('require_approval', 'Agent is configured to require approval for every mutation')
    return Decision('allow', 'Predefined low-risk action is within policy')
