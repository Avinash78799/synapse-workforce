# Requirements and implementation status

**Current implementation (26 September 2026): sandbox-tested; live business-vendor integrations are not verified.** Read-only Zendesk OAuth, the Trust Dashboard, internal founder onboarding and a narrow triage demo are implemented in source. Current verification: 129 passed, 105 skipped, two tracked warnings; TypeScript passed, full Next.js build blocked by Windows process-creation permissions. Database migrations, native integration tests and deployment of these changes are pending. Gate 1 passed against the previous deployment. The historical 185-test native result does not qualify this change. See [current evidence and limitations](trust-milestone-20260926.md), [backend gate order](backend-completion-plan.md) and [client wording](client-readiness.md).

The older blueprint mapping below is historical. The current backend gate plan and trust milestone take precedence over its older “next work” list.

Vendor account registration and bounded read-only Shopify/Zendesk inspection are implemented; see [vendor connections](vendor-connections.md). These checks do not enable autonomous vendor actions or establish live-vendor verification.

The source of truth is Blueprint v1.2, especially the V1 scope lock in §44. The supplementary prompts/skills document tightens the execution boundaries without moving voice, additional agents, or real integrations into V1.

“Implemented” below means source exists and is wired into the V1 simulator. It does **not** mean production deployment, external-provider behavior, or all enterprise failure modes have been qualified. See `verification.md` for measured evidence.

| Blueprint section | Delivery |
| --- | --- |
| 1–2: Product vision and outcome-first execution | Persisted goals become controlled workflows, verified actions, and reports; dashboard is an operating surface. |
| 3: Two specialized agents | Operations monitoring/recovery/tasks/escalation; Support customer/order lookup, duplicate investigation, refunds, ticket updates, and simulated communication. Repeated claims use a configurable history window. |
| 4: Agent isolation | Concrete tenant and agent RLS scope for records, tools, memory, policies, approvals, evaluations, and credentials. Independent simulator copies. |
| 5: Shared core | Model boundary, structured planning, tool registry, execution receipts, state, memory, bounded retries, and run history. Advanced provider routing is future work. |
| 6–8: Architecture, control plane, identity | Next.js control plane; FastAPI; agents with immutable versions, owner, risk, role-specific tools, status, and scoped audit. |
| 9: Policy and approval mechanics | Deterministic rules, immutable versions, exact-action digest, named approver roles, expiration/default deny, dashboard notification, and resumption. |
| 10: Autonomy | Agent levels 0–5 enforce read/prepare, approval, and controlled workflow execution. Levels 0–2 share the no-mutation boundary; distinct recommendation UX is not implemented. Level 3 requires approval for every mutation; levels 4–5 currently share behavior. Promotion requires 100 measured runs, ≥95% completed, zero recorded unauthorized incidents, and owner sign-off. Per-action thresholds exist for refunds/retries; per-skill autonomy caps are exposed and can only restrict agent authority. |
| 11: Verification and idempotency | Stable keys incorporate tenant, agent, run, task, entity, and action. Receipts and simulator ledger read-back prevent blind duplicate execution. UNKNOWN freezes for reconciliation. |
| 12–13: Resumable/event-driven work | Durable approval waiting/resumption and expiry wakeups are implemented. General scheduled operations, external events, and multi-day business workflows remain V2. |
| 14–15: Skills and tools | Separate registries and activatable skills/tools. Every tool passes the execution gate, including simulation writes. Some composite reads require several skills together. |
| 16–17: Hermes, MCP, A2A | Deferred as specified. No automatic inter-agent communication or dependency on Hermes. |
| 18: Voice | VoiceProvider protocol boundary only. Fish Audio, streaming, interruptions, telephony, and languages are later-phase work. |
| 19: Memory | Working/episodic memories from runs; scoped semantic/procedural bootstrap memories. No cross-agent memory access. |
| 20: Knowledge/RAG | Text ingestion, chunking, pgvector storage, materialized pre-filtering by both scopes, cited extractive evidence, and the exact missing-evidence response. Default lexical vectors are simulation-only. Live semantic embeddings and document-file ingestion require a provider/next iteration. |
| 21: Injection defense | Typed arguments, server-owned identity, metadata mismatch rejection, tool allowlists, and deterministic policy. Retrieved content never authorizes a write. Redaction prevents PAN-like data from entering goals, documents, or decision reasons. |
| 22: Digital twin | Seeded Acme data, inspectable state changes, four support scenarios, operations failure/recovery cases. Large historical replay and advanced simulation are future work. |
| 23–24: Evaluation/regression | 12-case measured policy contract suite with saved results; unit/security/integration tests for workflows and isolation. No unmeasured model-quality claims. Full model benchmark datasets remain a pre-production task. |
| 25: Observability | Run console, step status, business-safe summaries, tools, receipts, verification, time, model usage, configured cost, and failures. Distributed tracing/advanced telemetry are future work. |
| 26: Audit | Append-only app privileges; actor, tenant, agent/version, tool, policy version, approval, timestamp, and result. V1.1 adds authenticated hash chains and verified exports. External checkpoint retention/WORM remains deployment work; historical unsigned rows are labeled. |
| 27–28: Security/governance | Restrictive FORCE RLS, separate auth/app/dispatcher roles, sessions and CSRF, secret references/Vault adapter, rate/action/token/retry controls, conservative tenant model-spend reservations, graceful pause, and supervised hard abort. HTTPS, managed secrets, backups, SSO, and independent security review are deployment work. |
| 29–31: Acme and demos | All named simulator entities represented in scoped typed record kinds. Complete Operations and Support workflows are connected to the UI. |
| 32: Dashboard | Workforce cards, run console, approvals, simulation browser, evaluations, audit, agent configuration, knowledge, memory, lifecycle controls, and measured workspace counts/rates. |
| 33: ROI | No fabricated hours or savings. UI states that no baseline is configured. Full ROI events/analytics are V2. |
| 34: Data model | Scope-owned schema, receipts/cases/outbox, knowledge vector tables, sessions/auth directory. Workflow executions reuse agent_runs; metric reports reuse run summaries. No redundant empty tables are added solely to match example names. |
| 35–36: Stack/repository | Next.js + TypeScript + Tailwind; Python + FastAPI + Pydantic; PostgreSQL + pgvector; Redis; Python workers. Modular repository follows the named boundaries. S3 storage and browser automation are unnecessary for the locked V1 text simulator. |
| 37: Roadmap | The original blueprint phases 0–6 (not the new market-service roadmap phases) have a V1 implementation with the qualification limits above. Later phases stay outside current scope. |
| 38: What not to build | No generic agent builder, marketplace, extra agents, autonomous high-risk payments, or premature voice. |
| 39–40: Differentiators/success criteria | Verifiable execution and measurable policy outcomes implemented. Target percentages from the blueprint are not claimed as achieved production performance. |
| 41: Business model | Pricing/subscription billing intentionally omitted. |
| 42–46: Experience/scope/principle/definition | Core-first build, then connected control plane. V1 is explicitly simulation. NeuraSearch remains out of scope. |
| 47–48: Review fixes | Governance before dispatch; idempotency; entity-level dedup; approval roles/timeouts/pinning; concrete promotion gate; scoped vector retrieval; RLS; Vault references; token-only payment records; dual-mode kill; repeat-claim review. |

## Supplementary document mapping

| Enhancement | Implementation |
| --- | --- |
| Strict isolated RAG | `core/memory/knowledge.py`, restrictive database scopes, exact fallback constant, cited extraction in API/UI. No retrieved instructions execute. |
| Structured metadata | Typed Suggestion includes tenant UUID, agent scope, target entity, advisory risk, vector query, intent, and summary. Live output must match the server envelope. |
| Abuse signals | Scoped ticket and claim history counted inside a configured rolling window; current provider evidence refreshed before refund policy. |
| Autonomous plan compiler | Entity-bound actions generated from simulator evidence, with an explicit VERIFY entry after every mutation. |
| Reconciliation | Fresh provider-state verification; UNKNOWN status blocks re-execution and leaves an audit trail. |
| Restrictive RLS | A permissive role grant is paired with restrictive tenant/agent policies. The document's restrictive-only sample is supplemented because PostgreSQL requires an applicable permissive policy too. |
| Secret mapping | Immutable UUID references, scoped Vault paths, uncached lookup with active/epoch checks; values never enter business tables/prompts. |
| Idempotent hook | SHA-256 of tenant/agent/run/task/entity/action; persisted before dispatch and bound to an argument digest. |
| Dual kill semantics | Graceful drain; hard-abort execution epoch; supervisor process termination; owner password before unlock. Remote accepted mutations remain subject to reconciliation. |

## Next concrete work, in order

Follow [backend-completion-plan.md](backend-completion-plan.md): qualify Gate 2 OAuth implementation, then real Zendesk lifecycle (Gate 3), then controlled writes (Gate 4). Per-action automation and reversal follow Gate 4. Preserve the live-vendor qualification caveat.

The roadmap review incorrectly grouped autonomy levels 3–5 together: level 3 already required every mutation to be approved. We preserved that behavior and clarified the shared 0–2 and 4–5 boundaries.

Latest local deployment evidence and remaining backend work: [deployment verification](deployment-verification-20260921.md).
