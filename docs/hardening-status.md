# Production roadmap response — V1.1

**Historical report.** For the current client milestone, use [client readiness](client-readiness.md) and [verification follow-ups](verification-followups.md). Sandbox tests and earlier LLM provider checks do not establish live business-vendor verification or production readiness.

The supplied `production-roadmap-review.txt` is retained unchanged. This update implements the local engineering work described below; it does not claim that the market-service roadmap is complete.

**Current evidence:** 72 automated checks passed, 9 native-only checks skipped. Executed database checks used PGlite with explicit transaction-local roles; browser checks used a lightweight dispatcher. Native login authentication, concurrency, the real supervisor, Docker boot, and live provider billing remain unverified here.

## Immediate review items

| Review item | Result in this update | Remaining boundary |
| --- | --- | --- |
| A1: accurate reporting | Test-harness caveats moved alongside result headlines. Docker definitions labeled as supplied but unbuilt/unbooted. Browser dispatcher identified. | No claim of native or production qualification. |
| A1: autonomy clarity | UI/docs state 0–2 share preparation, 3 requires every mutation to be approved, and 4–5 share controlled behavior. | Review's claim that 3–5 were identical was incorrect; six distinct UX behaviors are not claimed. |
| A2: owner self-approval | Configurable financial threshold, inclusive at the boundary; requester cannot approve or deny required independent review. Required independence is pinned and rechecked at dispatch. | Applies to same authenticated identity. Demo passwords/accounts must be replaced by separate real identities before deployment. Owners can configure policy thresholds for new work, with an audit record. |
| A2: native RLS/startup | Dedicated-role checks strengthened; native startup, real roles, and concurrent case/decision tests supplied. | Could not execute: Docker/PostgreSQL unavailable; package installation prevented by environment restrictions. |
| A2: approval abuse | Atomic actor/tenant limits, repeated-authorization-failure blocks, durable bounded signals and UI. | Rule-based thresholds; no statistical anomaly model or external alert integration. |
| A2: stale secrets | Removed reusable Vault integration cache; active/epoch checks before and after lookup. | Managed Vault/KMS and remote credential revocation require deployment credentials. |
| A3: model budget | Tenant daily budget, reserved capacity, recorded run costs, uncertainty count, and per-run usage UI. | No live provider connected. UTC totals group runs by creation date; not provider invoice reconciliation. |
| A3: UNKNOWN review | Scoped review queue, argument verification, fresh evidence check, close-unresolved action, role checks, audit history. | No manual "force success," blind retry, automatic customer message, or run resumption. Live-provider evidence contracts await a connector. |
| A3: audit protection | Per-agent authenticated hash chains, verified export, offline verifier, retained-checkpoint comparison, key rotation support. | Symmetric HMAC, not public-key signing/WORM. Independent checkpoint retention is needed to detect tail deletion. DBA + key/runtime compromise is outside this protection. Legacy events remain unsigned. |
| A3: skill autonomy | Configuration caps can restrict each required skill; effective authority cannot exceed the agent. | Increasing the agent's overall authority still follows its promotion gate. |
| A3: semantic embeddings | Existing provider boundary retained; lexical simulation remains clearly labeled. | Await chosen provider/model, dimensions, price/retention requirements and credentials before reindexing. |
| A4: security/failure tests | Added session replay/rotation/CSRF, expiry at boundary and time offsets, owner/role blocks, budgets, adversarial mocked model outputs, audit tampering, and reconciliation tests. | Mocked model contracts do not measure real LLM behavior. |
| A4: native concurrency/chaos | Supplied competing-approver, concurrent Operations, lost Redis acknowledgment, crash-mid-refund, actual hard-abort and startup tests; cold-start/Redis-outage gate. | These are prepared, not passed. High-load benchmarking, live-provider timeout semantics, and broader process-recovery tests remain. |

## Additional issue found and fixed

The previous hard-abort transaction could wait for a provider-held receipt lock before the durable stop state became visible. V1.1 commits the stop epoch first, marks unlocked receipts without waiting, and lets the supervisor terminate the child before freezing remaining dispatched receipts. A native regression test is supplied. Finalization also preserves an already-aborted run.

Unexpected child exits now freeze dispatched receipts instead of leaving them eligible for a blind subsequent dispatch. The simulator can still reconcile confirmed receipts from its atomic ledger. None of this establishes cancellation of a remotely accepted payment.

## Six-phase market-service roadmap

| Phase | Work provided now | Needs user/external participation |
| --- | --- | --- |
| 0: validate a wedge | Proposed duplicate-charge investigation/refund workflow, interview guide, decision criteria, metric definitions in `product-validation.md`. | Select target customer/stack; conduct 10–15 interviews and recruit design partners. No interviews or outreach performed. |
| 1: real infrastructure | Disposable native gate, cold-start timing, independent approval through real workers, Redis outage recovery, CI definition. | Docker-capable host; execute gates; choose live model and managed secrets provider; configure credentials. |
| 2: security/compliance | Approval separation, session hardening, rate/anomaly signals, authenticated audit, additional tests. | Independent penetration test and jurisdiction-specific privacy/payment/compliance review. No legal approval or certification claimed. |
| 3: integrations | Connector acceptance checklist and preserved adapter boundaries. | Choose ONE ecommerce/helpdesk/payment stack; provide sandbox app/account access; implement OAuth/webhooks and qualify that provider. |
| 4: operations | Database/queue readiness, worker heartbeat, structured request timing, disposable CI gate, incident/backup/restore/alert runbook. | CI execution, staging/production hosts, monitoring destination, scheduled backups, tested restoration, operational ownership. Full deployment CD/tracing not claimed. |
| 5: company/legal/billing | Dependencies and review needs recorded. | Founder/company decisions, legal counsel, business/payment accounts, pricing and support commitments. No registration, billing, or legal documents were published. |
| 6: market launch | Pilot scorecard and stop/go criteria. | 1–3 actual design partners, measured usage, chosen pricing, customer permission for a case study. No customer traction is claimed. |

## Next decisions

1. Provide a Docker-capable host or GitHub repository/CI environment for the native gate.
2. Select the first integration stack and target customer. Proposed lead workflow: duplicate-charge investigation with independently approved refunds.
3. Select a model provider/model and managed secrets/hosting service. Configure keys through the chosen secrets service, not in chat.
4. Arrange real user provisioning and an external security review before handling customer data or real money.

Run `python infrastructure/native_gate.py` first. Preserve the resulting evidence, resolve failures, then qualify one live model and one sandbox connector. The generated CI workflow builds/tests only; it does not deploy to production.
