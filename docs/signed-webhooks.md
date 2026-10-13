# Signed incoming events

This is a custom-backend protocol for managed sandboxes, not a Shopify or Zendesk native webhook adapter. Business-vendor connectivity remains unverified. It lets a backend start a controlled workflow and track receipt without granting approval or administration authority.

## Deployment

For an existing installation, run `python infrastructure/configure_webhooks.py` before starting the updated Compose stack. It creates `.secrets/webhook_signing_key` once without replacing `.env` or an existing secret. New installations include this step in `configure_local.py`. Keep the parent secrets directory private; use deployment-managed secret storage outside local development.

Apply migration `007_signed_webhooks.sql` after 006 with the migration-owner service and rebuild API, worker and web from the same revision. Only the API mounts this signing key. A missing/short key fails closed with 503; it does not fall back to the audit key or a hard-coded default. No runtime migration or data reset is needed.

## Create and send

1. In a managed workspace, connect all three sandbox categories.
2. Open **Webhooks**, select a Support or Operations agent and create an endpoint. Only the owner can create, rotate or revoke.
3. Copy the endpoint path and one-time signing secret to the sending backend. Do not embed it in a website/mobile frontend or include it in reports.
4. POST JSON using `Content-Type: application/json`, `X-Workforce-Timestamp` as Unix seconds and `X-Workforce-Signature` as `v1=` followed by a lowercase hex HMAC-SHA256. Sign `timestamp + "." + exact_body_bytes` with the whole displayed `whsec_...` secret as the UTF-8 HMAC key. Do not send browser cookies, Origin or compressed bodies.

Support payload:

```json
{"event_id":"<UUID>","event_type":"support.duplicate_charge","customer_id":"CUS-001","order_id":"ORD-1001"}
```

Operations payload:

```json
{"event_id":"<UUID>","event_type":"operations.review"}
```

The endpoint determines the tenant, agent and initiating owner; clients cannot supply them or arbitrary tools, actions, permissions or an objective. The support customer/order binding is checked against scoped authoritative sandbox records before accepting a job. Operations events accept no customer/order fields. Existing policy, consent, agent status, approval and independent-review checks still apply. The endpoint owner is the requester; a webhook cannot manufacture a separate human approver.

Example sender using Python's standard library (save valid JSON once in `event.json` and set the three environment variables on your backend):

```python
import hashlib, hmac, os, time, urllib.request
from pathlib import Path

raw = Path('event.json').read_bytes()
timestamp = str(int(time.time()))
secret = os.environ['WORKFORCE_WEBHOOK_SECRET']
signature = 'v1=' + hmac.new(secret.encode(), timestamp.encode()+b'.'+raw, hashlib.sha256).hexdigest()
request = urllib.request.Request(
    os.environ['WORKFORCE_BASE_URL'].rstrip('/') + os.environ['WORKFORCE_WEBHOOK_PATH'],
    data=raw, method='POST', headers={
        'Content-Type':'application/json',
        'X-Workforce-Timestamp':timestamp,
        'X-Workforce-Signature':signature,
    })
with urllib.request.urlopen(request, timeout=15) as response:
    print(response.status, response.read().decode())
```

Use HTTPS and trust your deployment certificate. For a local self-signed deployment, configure the sender's CA trust rather than disabling certificate checks. No external event was sent as part of implementing this example.

## Delivery semantics

- HTTP 202 means the job was durably queued or a previously accepted event was found; it does not mean that business work completed. Inspect the run through the dashboard or the separately scoped service-key API.
- Signatures must be within five minutes of the server clock. For a retry after a timeout, keep the same event UUID and exact JSON bytes and sign them with a fresh timestamp. Changed bytes under the same event ID return 409, even if JSON formatting was the only difference.
- Concurrent matching events serialize on the agent and produce one run submission. Receipt, run, business-case reservation and queue outbox commit in the same business transaction. Database failures roll them all back.
- Signed, schema-valid events rejected by readiness/binding checks return 409 with a stable reason code. They are retained without raw payloads. After correcting workspace readiness, the identical event can be resent; its row records the latest outcome and count. Changed payloads need a new event ID.
- Existing business-case deduplication still applies across different event IDs/endpoints. In particular, a support case is not refunded again because another trigger uses a new ID.
- Invalid signatures/expired timestamps return 401; invalid schemas 422; wrong media/encoding 415; bodies over 32 KiB 413. Streaming/chunked bodies are subject to the same cap. Missing signing configuration or master-key mismatch returns 503.
- Endpoint intake is limited to 120 requests/minute, source IP to 600/minute, and new submissions share the existing per-agent hourly run limit with API/dashboard triggers. 429 attempts are not accepted. The source IP is the directly connected peer; behind the current proxy this can be shared by senders. Do not trust arbitrary forwarded-IP headers to bypass that ceiling.
- Accepted duplicates bypass the hourly submission limit, but still count toward intake limits. Keep a sender-side outbox and bounded exponential retry/backoff for timeouts, 429 and transient 5xx responses. After a 409 readiness error, correct the reported problem first.

## History, secrets and revocation

Workspace members can view the latest 100 authenticated, schema-valid event rows per endpoint, with outcome, reason, matching attempt count, timestamps and run link. This is a recent event ledger, not a complete log of every network request: invalid signatures, malformed schemas, rate limits and conflicting duplicate payloads do not add rows. Readiness rejections and accepted submissions also create signed platform audit events. No raw request payload, signing header, endpoint secret or arbitrary third-party error is stored in the event ledger.

Each secret is derived with domain-separated HMAC from an API-only master key and the tenant, agent, endpoint UUID and random generation. SQL stores only binding metadata, generation and a master-key fingerprint. Endpoint rotation changes its generation immediately; the old secret stops validating. Revocation blocks future submissions but does not cancel accepted jobs. Both wait for any already-authenticated in-flight intake transaction holding the endpoint lock to finish.

Back up the master outside SQL/source control. Replacing it invalidates all old endpoint secrets: endpoints display “rotation required” and reject delivery with 503 until the owner rotates and updates each sender. This master protects webhook signing only; it is not a general vendor-credential vault. Vendor OAuth/tokens remain a separate implementation step.
