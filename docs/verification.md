# Verification report — V1.1, 16 September 2026

**72 passed, 9 skipped, 0 failures/errors — using PGlite with explicit transaction-local database roles, not native PostgreSQL login authentication or concurrency. Browser journeys used a lightweight local dispatcher, not the real worker supervisor. Docker definitions were parsed, not built or booted.**

The baseline V1 report recorded 46 passed and 1 native-only skip under the same harness limitations. V1.1 adds meaningful hardening checks and a larger native-only gate; it does not convert the baseline into production qualification.

## Recorded results

| Area | Result | Scope of evidence |
| --- | --- | --- |
| Unit/policy | 23 passed | Baseline contracts plus inclusive independent-review threshold, non-bypass by a higher automatic limit, and actual autonomy behavior. |
| Workflow/API/provider-contract | 39 passed | Baseline workflows plus independent requester/approver enforcement, pinned independence, rate and role abuse, exact expiry with timezone offsets, session rotation/replay/CSRF, skill caps, safe UNKNOWN review, audit tamper/checkpoint detection, uncached Vault boundary, budget exhaustion, and adversarial mocked model output. |
| SQL isolation/security | 10 passed | Tenant/agent read/write boundaries, scoped retrieval, audit/policy immutability for application roles, private auth directory, forged scope/CSRF, viewer restrictions, missing-scope denial. |
| Native-only checks | 9 skipped | Native lifecycle/roles, privileged-role rejection, concurrent case/Operations creation, competing approvers, actual supervisor crash/hard abort, and lost Redis acknowledgement recovery. |
| Frontend production build | Passed | Next.js compilation, TypeScript, and route generation. |
| Browser journeys | Passed | Requester cannot approve own high-value refund; separate finance login approves and reaches verified completion; frozen-action review without redispatch; budget screen; authenticated audit download; skill cap/policy configuration; baseline Operations/evaluations. |
| Responsive checks | Passed | 1440×1050 desktop and 390×844 mobile. Workforce, budget, and reconciliation checked; no page errors or document-level horizontal overflow. Wide tables scroll within their container. |
| Configuration/source checks | Parsed/compiled | Both Compose files and CI YAML parsed; Python source compiled. This does not establish Docker compatibility or successful native execution. |
| OpenAPI | Generated | 26 documented API paths. |

Two existing dependency deprecation warnings occurred in the test client. They are recorded in the saved log and did not fail the suite.

## Saved evidence

- `artifacts/test-results.xml`: latest machine-readable 72/9 test result.
- `artifacts/hardening-tests.txt`: full latest local test output.
- `artifacts/frontend-build-v1.1.txt`: frontend build output.
- `artifacts/browser-results-v1.1.json`: latest browser result and journeys.
- `artifacts/reconciliation-v1.1.png`, `budget-v1.1.png`, `budget-mobile-v1.1.png`: new screens.
- `artifacts/workforce-desktop.png`, `workforce-mobile.png`, `approval-review.png`, `support-resolution.png`: updated existing views.
- `artifacts/browser-audit-export.json`: authenticated fictional-data export from the browser test, not a production audit.
- `artifacts/native-status.json`: explicit unexecuted-native status.

## Database and browser harness limitations

The execution environment had no Docker/native PostgreSQL service. A package-install attempt was blocked by environment identity/permission restrictions. Database-backed checks therefore used PGlite 0.5.8 (PostgreSQL 18.3 compiled to WebAssembly) with pgvector and a real Redis process.

PGlite has a single backend and does not authenticate native PostgreSQL login roles. The harness applies SET LOCAL ROLE wf_app/wf_auth inside transactions and serializes access. This exercises shipped SQL policies and application logic, but cannot establish native login enforcement, connection-pool isolation, or multi-process contention.

The browser harness runs real HTTP handlers and persisted simulator state with production lifespan disabled and a lightweight local outbox dispatcher. It does not qualify Redis Streams supervisor/reclaim behavior or process termination. Production source retains startup role/key checks; the harness does not weaken delivered startup code.

Live model contract tests mock HTTP responses and Vault retrieval while exercising real Redis reservations. They do not verify an actual LLM, semantic embeddings, provider prices, billing semantics, or prompt resistance across real model outputs.

## Prepared native gate — not executed

On a Docker-capable host:

```sh
python infrastructure/native_gate.py
```

The gate supplies native PostgreSQL 17 + pgvector and Redis 7.4, runs the native test suite, builds and boots the whole application, records cold-start time, verifies an independently approved refund through the real supervisor, and checks queued work across a real Redis outage. It creates and removes only its own isolated temporary stacks. `.github/workflows/verify.yml` is supplied to run this and the web build in CI; it has not been installed or run in a hosted repository here.

## Remaining gates

1. Run and pass the native gate; investigate any actual concurrency, startup, crash, drain, recovery, or timing failures. Broader load, expiry-wakeup and graceful-drain testing remains beyond the supplied cases.
2. Qualify one live model, real usage/pricing limits, and managed secrets. Choose a semantic embedding model and reindex only after dimension/provider compatibility is defined.
3. Retain audit checkpoints outside PostgreSQL; validate key backup/rotation and compare checkpoints after a restore. Hash chains/HMAC alone do not prevent deletion or protect a compromised signing runtime.
4. Run an independent penetration test and relevant privacy/payment/compliance review.
5. Configure staging/production hosting, TLS, real separate user identities, backups/restores, monitoring, alerts, and operational ownership.
6. Qualify one chosen sandbox business connector before any real customer data or money. Complete a broader agent-quality benchmark and supervised design-partner pilot.

No claim is made of production readiness, regulatory certification, measured ROI, live customer resolution, or completion of the market-service roadmap.
