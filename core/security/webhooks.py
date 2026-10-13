"""Purpose-separated per-endpoint HMAC secrets; raw keys never enter SQL."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import time

MAX_BODY_BYTES = 32_768
MAX_CLOCK_SKEW = 300


def master_key():
    key = Path(os.getenv('WEBHOOK_SIGNING_KEY_FILE', '/run/secrets/webhook_signing_key')).read_bytes().strip()
    if len(key) < 32:
        raise ValueError('Webhook signing key must contain at least 32 bytes')
    return key


def key_id(key):
    return hashlib.sha256(key).hexdigest()[:16]


def endpoint_secret(endpoint, key):
    context = '|'.join(('workforce-webhook-v1', str(endpoint['tenant_id']),
                        str(endpoint['agent_id']), str(endpoint['id']), str(endpoint['generation'])))
    return 'whsec_' + hmac.new(key, context.encode(), hashlib.sha256).hexdigest()


def valid_signature(secret, timestamp, signature, raw, now=None):
    if not re.fullmatch(r'[0-9]{10}', timestamp or ''):
        return False
    if not re.fullmatch(r'v1=[0-9a-f]{64}', signature or ''):
        return False
    if abs((time.time() if now is None else now) - int(timestamp)) > MAX_CLOCK_SKEW:
        return False
    expected = 'v1=' + hmac.new(secret.encode(), timestamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
