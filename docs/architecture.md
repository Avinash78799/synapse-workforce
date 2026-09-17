# Architecture and security decisions

## Runtime path

The browser submits an objective to the same-origin Next.js `/api` proxy. FastAPI derives tenant and role from an authenticated server-side session. It does not accept a tenant ID from the body, URL, model, document, or customer message. API mutations require the session CSRF token. Business requests receive a transaction-local tenant/agent scope.

The API persists a run, an entity case, and an outbox event together. The narrow dispatcher role can only read and mark the outbox; it cannot read business records. It publishes identifiers to Redis Streams. Python workers reclaim abandoned jobs and acquire a per-run advisory lock. The selected agent sees only its own simulator, policies, tools, knowledge, and memory.

The model is allowed to classify intent and produce advisory metadata. The server validates the metadata against its authenticated envelope. A deterministic workflow compiles concrete actions from verified business records. Model-supplied risk classifications cannot lower policy risk or grant permission.

Every action is typed and passed through the policy gate. Before dispatch, the runtime checks the current agent state, execution epoch, tool permission, required skills, pinned rules, and any approval. It creates a durable receipt and logs dispatch. The simulator commits a mutation and its result receipt atomically. Verification reads the business state back in a new transaction. Ticket closure and customer success communication depend on verified refund completion.

## Data ownership and database roles

- `wf_app`: ordinary NOSUPERUSER/NOBYPASSRLS business role. Forced restrictive policies require tenant and agent context. It cannot change schemas, overwrite audit rows or policy versions, or read the private auth directory.
- `wf_auth`: only reads the private login directory and manages session records. Login discovery is necessarily outside the business RLS scope, but its credentials are isolated from the business role. Sessions include tenant ID and only join matching users.
- `wf_dispatch`: reads and marks job outbox rows; cannot query business or credential tables.
- Migration owner: creates roles/schema/extensions and seeds simulations. Never provided to API or worker services.

The `control` agent scope is used only by authenticated operator/control-plane code to inspect agents within the user's tenant. Workers always set a concrete agent UUID. SQL is parameterized; models never receive SQL execution or arbitrary database connections. Transaction-local settings do not leak through a pool checkout.

Operations and Support have separate copies of simulator business data. There is no implicit context sharing, even for matching order numbers. Future real integrations must explicitly grant and audit the relevant scopes.

RLS uses a permissive runtime-access policy paired with a restrictive tenant/agent boundary. A later permissive feature policy cannot expand that boundary. pgvector retrieval uses a materialized scope-filtered CTE before similarity ranking and is additionally subject to RLS.

## Financial correctness

Amounts use integer minor units. Refunds must match customer ownership, order, captured payment, currency, and duplicate amount in provider state. Only tokenized simulation references are stored; no card numbers are modeled.

The persisted idempotency key hashes tenant, agent, run, task, concrete entity, and action. A repeated request uses the stored task/key. A digest rejects changed arguments under an existing key. Entity cases deduplicate independent triggers with different request keys. A payment row lock and a unique simulated refund ID provide another boundary against duplicate refunds.

Confirmed receipts are verified rather than dispatched again. A dispatched simulator receipt can be reconciled against its atomic local provider ledger. An explicitly UNKNOWN state freezes the action for human reconciliation. An external adapter may retry only when it can prove absence/failure safely; it must never interpret an ambiguous timeout as permission to replay.

## Approval rules

Default automatic refund limit: ₹5,000, with independent approval required at or above ₹5,000; the more restrictive rule wins. Claims with a count of three or more require approval regardless of amount. High-value or repeated-claim refunds require the `finance_approver` role; the named `owner` role is also authorized. Operators and viewers cannot decide. V1.1 binds independent review to the run requester and the approval record. At or above the configured financial threshold, the requester cannot decide, even as an owner. The runtime checks this again before dispatch. Existing pending approvals cannot be relaxed by changing the policy version. Below that threshold, an approval required for another reason can still be decided by an otherwise authorized requester.

Approvals pin the task's immutable policy version and bind to a digest of its exact action. Default deadline: 60 minutes, configurable between 1 minute and 24 hours. Expiry means deny. A delayed outbox event wakes the run at expiry; the API also checks the timestamp during a decision. Pending approvals are visible in the dashboard. Email/Slack delivery is not simulated as a real notification.

New policy versions apply to new runs. Existing runs remain reproducible under their pinned rules. Current tool/skill revocations and kill controls still prevent a new dispatch even after human approval.

## Kill switch

Graceful pause blocks a new run or undispatched step. Already-dispatched calls finish and verify, then the run pauses. Reactivation requeues paused runs.

Hard abort commits the agent execution epoch and aborted run state before touching provider-held receipt locks. The API marks unlocked dispatched receipts UNKNOWN; the supervisor terminates the child and freezes any remaining dispatched receipts. This avoids delaying the emergency stop behind the provider transaction. A worker supervisor checks durable agent state every 200 ms while a subprocess runs and terminates a hard-aborted subprocess. This is cooperative control plus process termination, not a guarantee that a remote service can reverse an accepted request. Database/network stalls can increase the polling delay. An owner password check is required before any exit from hard-aborted state; old epochs remain invalid.

## Prompts and verification

The supplied prompt contracts are preserved verbatim as a reference document and adapted into a versioned Python module. Only metadata extraction is currently sent to a live model. Plan compilation, risk routing, verification, and extractive knowledge quotation have deterministic implementations. This keeps authorization independent from prompt compliance.

Every compiled mutation includes an entity ID and an explicit VERIFY step. Actual execution verifies every mutation, which is stricter than the enhancement's “at least every three steps” limit. Business-safe summaries and structured evidence appear in the console; hidden chain-of-thought is neither stored nor exposed.

## Source documentation used

The RLS design follows [PostgreSQL row-security documentation](https://www.postgresql.org/docs/current/ddl-rowsecurity.html), including owner bypass, non-bypass runtime roles, FORCE RLS, and restrictive policies. The same-origin API path uses [Next.js external rewrites](https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites). The local verification harness uses [ElectricSQL PGlite](https://github.com/electric-sql/pglite); its multiplexed single backend does not qualify native PostgreSQL connection authentication or concurrency.


## V1.1 operator and audit controls

Skill autonomy caps use the minimum of the agent level and all required skill caps. They cannot raise authority. Levels 0–2 share the preparation boundary; level 3 requires approval for every mutation; levels 4–5 share current controlled behavior.

Approval endpoints have Redis atomic actor/tenant counters, a temporary block after repeated authorization failures, and bounded durable security signals. Five rejected authorization attempts in five minutes block further attempts for five minutes. The ordinary limit is 20 decisions/attempts per actor per minute and 200 per tenant per minute. These are rule-based signals, not learned anomaly detection. They do not replace an independent penetration test.

The reconciliation endpoint acquires the run lock and narrows the control-plane database context to the action's exact agent before reading provider records. It verifies arguments and fresh provider state. Successful review records completion of that action; closing unresolved records a failure. Both leave the run escalated or aborted, never requeue it, and never dispatch another refund or customer message.

Audit events include sequence, previous hash, SHA-256 event digest, HMAC, and key identifier. Keys live in mounted files outside SQL. Chains are per tenant/agent to respect RLS. JSON exports authenticate the complete export and include stream checkpoints. Pre-upgrade rows remain explicitly unsigned. An administrator with only database access cannot forge valid authenticated event content, but suffix deletion requires comparison with an independently retained prior export. This is not WORM storage, a public-key signature, or protection against an attacker who also controls the signing key/runtime.

Secret retrieval is now uncached and checks active state/execution epoch before and after Vault lookup. This removes stale reusable cache entries across processes. It does not revoke an API request already accepted remotely or guarantee memory erasure of a value already handed to a caller.

The budget screen distinguishes conservative Redis reservations from recorded costs for runs created on the current UTC date. Per-run cost stays visible. Failed/ambiguous model requests retain reservations. Real provider billing, cross-day settlement, and reconciliation are still unqualified.

HMAC comparison follows https://docs.python.org/3/library/hmac.html. Store audit keys, prior verification keys, and independently retained checkpoints according to deployment access/retention policy.
