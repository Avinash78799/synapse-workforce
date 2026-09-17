"""Measured policy-contract checks, not claims about model intelligence."""
from core.policies.engine import PolicyRules, evaluate

SCENARIOS = [
    ('refund-low','Verified INR 2,400 duplicate','payment.refund',{'amount_minor':240000,'currency':'INR','duplicate_verified':True,'claim_count':1},True,4,'allow'),
    ('refund-high','INR 75,000 requires approval','payment.refund',{'amount_minor':7500000,'currency':'INR','duplicate_verified':True,'claim_count':1},True,4,'require_approval'),
    ('repeat-claim','Repeated low-value claims','payment.refund',{'amount_minor':150000,'currency':'INR','duplicate_verified':True,'claim_count':4},True,4,'require_approval'),
    ('no-evidence','Unverified duplicate claim','payment.refund',{'amount_minor':240000,'currency':'INR','duplicate_verified':False,'claim_count':1},True,4,'deny'),
    ('wrong-currency','Unapproved currency','payment.refund',{'amount_minor':240000,'currency':'USD','duplicate_verified':True,'claim_count':1},True,4,'deny'),
    ('permission','Missing refund permission','payment.refund',{'amount_minor':240000,'currency':'INR','duplicate_verified':True,'claim_count':1},False,4,'deny'),
    ('observe','Observe-only agent','task.create',{},True,0,'prepare'),
    ('always-approval','Approval-level agent','task.create',{},True,3,'require_approval'),
    ('retry-limit','Payment retry exhausted','payment.retry',{'retry_eligible':True,'retry_count':2},True,4,'deny'),
    ('retry-eligible','Eligible payment retry','payment.retry',{'retry_eligible':True,'retry_count':0},True,4,'allow'),
    ('delete','Prohibited account deletion','customer.delete',{},True,5,'deny'),
    ('zero-refund','Zero refund amount','payment.refund',{'amount_minor':0,'currency':'INR','duplicate_verified':True},True,4,'deny'),
]


def run_suite():
    results=[]
    for identity,name,tool,args,allowed,autonomy,expected in SCENARIOS:
        decision=evaluate(tool,args,PolicyRules(),allowed=allowed,skill_enabled=True,autonomy=autonomy,mutates=True)
        results.append({'id':identity,'name':name,'expected':expected,'actual':decision.outcome,'passed':decision.outcome==expected,'reason':decision.reason})
    return {'suite_version':'policy-contracts-1.0','passed':sum(x['passed'] for x in results),'total':len(results),'results':results,
            'scope':'Deterministic policy contracts with baseline rules. Does not measure model accuracy, live integrations, or current custom-policy quality.'}
