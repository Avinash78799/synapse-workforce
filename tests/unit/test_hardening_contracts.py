from core.policies.engine import evaluate,PolicyRules


def test_financial_threshold_is_inclusive_and_cannot_be_bypassed_by_auto_limit():
    rules=PolicyRules(auto_refund_limit_minor=10_000_000,independent_approval_threshold_minor=500_000)
    args={'amount_minor':500_000,'currency':'INR','duplicate_verified':True,'claim_count':1}
    assert evaluate('payment.refund',args,rules,allowed=True,skill_enabled=True,autonomy=5,mutates=True).outcome=='require_approval'
    args['amount_minor']=499_999
    assert evaluate('payment.refund',args,rules,allowed=True,skill_enabled=True,autonomy=5,mutates=True).outcome=='allow'


def test_level_three_requires_mutation_approval_while_four_and_five_share_behavior():
    for level,expected in [(0,'prepare'),(1,'prepare'),(2,'prepare'),(3,'require_approval'),(4,'allow'),(5,'allow')]:
        assert evaluate('task.create',{},PolicyRules(),allowed=True,skill_enabled=True,autonomy=level,mutates=True).outcome==expected
