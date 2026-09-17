"""Approval throttling and durable security signals; authorization stays in API/runtime."""
from fastapi import HTTPException
from core.db import scoped
from core.runtime.audit import audit

COUNTER="local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n"


def signal(redis,user,reason,approval_id=None):
    key=f"approval-signal:{user['tenant_id']}:{user['id']}:{reason}"
    # Bound security-log growth during a flood. Counters still count every attempt.
    if redis.set(key,'1',nx=True,ex=60):
        with scoped(user['tenant_id']) as conn:
            audit(conn,user['tenant_id'],None,'approval.security','unauthorized',actor=user['id'],
                  details={'signal':reason,'approval_id':str(approval_id) if approval_id else None})


def guard(redis,user):
    prefix=f"approval:{user['tenant_id']}:{user['id']}"
    blocked=redis.exists(prefix+':blocked')
    actor_count=redis.eval(COUNTER,1,prefix+':minute',60)
    tenant_count=redis.eval(COUNTER,1,f"approval:{user['tenant_id']}:minute",60)
    if blocked or actor_count>20 or tenant_count>200:
        signal(redis,user,'approval_rate_limit')
        raise HTTPException(429,'Approval attempts are temporarily limited; try again later',headers={'Retry-After':'60'})


def rejected(redis,user,reason,approval_id):
    prefix=f"approval:{user['tenant_id']}:{user['id']}"
    count=redis.eval(COUNTER,1,prefix+':rejected',300)
    if count>=5:
        redis.set(prefix+':blocked','1',ex=300)
    signal(redis,user,reason,approval_id)
