# AI Agent Workforce Platform — V1.1 hardening

A V1 simulation implementation with the V1.1 hardening update of **Blueprint v1.2**, incorporating the supplied **Production Prompts & Skills v1.0**. The source documents, including the latest production roadmap review, are preserved in `docs/`.

**Verification:** 72 tests passed and 9 native-only tests skipped using PGlite transaction-local roles and a lightweight browser dispatcher. Native PostgreSQL authentication/concurrency, the real supervisor, and Docker cold boot are **not verified** here. Compose/Docker definitions are supplied; images were not built or booted. See `docs/verification.md`.

Two independent workers operate on separate Acme Commerce simulation datasets: Operations and Customer Support. The control plane submits real persisted jobs, shows execution and verification, routes approvals, and manages permissions. The default model provider is explicitly **deterministic simulation**; it does not pretend to be a live LLM.

## Start locally

Requirements: **Docker Desktop with Compose**, Python 3.10+ for the one-time configuration script, and approximately 4 GB of free memory. No business accounts or paid API keys are required for simulation.

From the extracted project directory:

```sh
python infrastructure/configure_local.py
docker compose up --build -d
```

Open **http://localhost:3000**. Sign in with `owner@acme.local` and the `SEED_PASSWORD` value in the generated `.env` file.

The same local simulation password is assigned to these deliberately separate demo accounts:

| Account | Role |
| --- | --- |
| owner@acme.local | Configure agents, submit work, approve financial actions, emergency controls |
| operator@acme.local | Submit work and inspect results; cannot approve refunds |
| finance_approver@acme.local | Review and approve or deny pending financial actions |
| viewer@acme.local | Inspect the workspace |
| owner@northstar.local | Separate tenant for isolation checks |

These are local simulation accounts. Do not publish them as production user accounts.

First startup builds the services, migrates Postgres, seeds the fictional companies once, then starts the API, worker, and web app. Subsequent starts preserve existing records. If startup fails:

```sh
docker compose ps
docker compose logs --tail=80 migrate seed api worker web
```

`docker compose down` stops the stack and preserves database volumes. Do not add `-v` unless you deliberately want to erase the simulation data.

## Upgrade an existing V1 simulation

Preserve your database and existing `.env`. Run `python infrastructure/configure_audit.py` once to create the separate audit key, then rebuild with `docker compose up --build -d`. Migration `002_hardening.sql` is additive and preserves prior policy/audit records. Do not edit the checksum-protected initial migration. Save the audit key independently of the database.

## Try the complete workflows

1. **Operations:** select **Run agent**, keep “Run today's operations,” and start. The worker checks 12 orders, retries eligible failed payments, requests shipment follow-ups, creates inventory tasks, and surfaces unresolved exceptions. A failed retry is reported honestly; it does not become a successful payment in the report.
2. **Low-value support case:** choose Aarav / ORD-1001. The agent verifies a ₹2,400 duplicate, refunds the duplicate payment, reads the state back, resolves the ticket, and records a customer message in the simulation outbox.
3. **High-value support case:** choose Emma / ORD-1002. The ₹75,000 duplicate waits for an independent owner or finance approver. If the owner requested it, sign out and sign in as `finance_approver@acme.local` to decide; the requester cannot approve or deny their own high-value action. Add a reason on the Approvals page. The worker resumes after the recorded decision.
4. **Repeated claims:** choose Riya / ORD-1003. The ₹1,500 claim requires approval because of the repeated-claim signal.
5. **No verified duplicate:** choose James / ORD-1004. The agent escalates the ticket without inventing a refundable payment.
6. **Controls:** open an agent to toggle skills/tools, create a policy version, adjust autonomy, ingest reference text, and inspect memory. Pause blocks new work. A hard abort invalidates execution context; unlocking requires the owner password.
7. **Evaluations:** run the 12-case policy contract suite. These are measured deterministic contract results, not model-accuracy or production-reliability claims.

Support requests for the same order attach to one persisted case, including after completion. Submitting the same order again will show the original case instead of refunding again. Restarting an already completed daily Operations run is permitted, while simultaneous daily triggers coalesce.

## What is implemented

- Next.js / React / TypeScript / Tailwind frontend; FastAPI / Pydantic backend.
- PostgreSQL + pgvector schema, checksummed migrations, Redis Streams, Python subprocess workers, and a transactional job outbox.
- Cookie-based sessions, CSRF checks, named application roles, and separate database roles for business data, authentication, and outbox dispatch.
- Forced, restrictive row-level security for tenant- and agent-owned records. Runtime roles cannot own or bypass the RLS boundary.
- A shared runtime with typed tools, deterministic policies, approval deadlines, pinned policy versions, durable execution receipts, entity deduplication, and read-back verification.
- Acme orders, customers, payments, inventory, products, shipments, tickets, staff, tasks, refunds, and an unsent message outbox, independently scoped per agent.
- Run console, independent approval review, per-skill autonomy caps, frozen-action reconciliation, model-budget reporting, knowledge retrieval, and working/episodic memory.
- Approval throttling and durable rule-based anomaly signals; session rotation and logout-replay protection.
- HMAC-authenticated per-agent audit chains, verified JSON exports, and an offline verifier. Independent retention of checkpoints is required to detect tail deletion; this is not write-once storage.
- Vault credential resolution using immutable UUID references with no reusable secret cache and agent state/epoch checks around retrieval. No integration API keys are stored in business tables or passed into prompts.
- A live chat-completions model adapter for **intent extraction only**. Tool selection and financial authority remain in the workflow/policy code.
- Prompt contracts for scoped RAG, metadata extraction, abuse signals, plan compilation, and verification; deterministic counterparts enforce the corresponding safety properties.

## Honest boundaries

This is a V1 simulation implementation, **not a certified enterprise production deployment**. The complete scope mapping and remaining gates are in `docs/implementation-status.md` and `docs/verification.md`.

- Business actions operate only on the simulator. No real emails, messages, payments, or refunds are sent.
- Default retrieval uses 64-dimensional hashed lexical vectors, clearly labeled as simulation retrieval. It is not a semantic embedding model. Results are source quotations; missing evidence uses the exact fallback specified by the enhancement document.
- Financial refund abuse detection counts scoped duplicate-charge/undelivered-order claims in a configurable 30-day window. Production history ingestion and fraud-provider signals belong with real integrations.
- Live model use requires a configured price schedule and positive daily tenant budget. Redis reserves a conservative request cost before dispatch and retains reservations after ambiguous failures. Request, token, run, action, and retry limits are also enforced. Live provider billing/usage accounting remains unverified until a provider is configured.
- The included Vault service uses **dev mode** for local integration work. It is ephemeral, uses a local bootstrap token, and must be replaced with managed Vault/KMS and scoped short-lived identities before hosting.
- Hard abort interrupts the local worker subprocess and invalidates run context. It cannot undo a mutation already accepted by an external provider. Unknown states require reconciliation; automatic re-execution is blocked.
- Voice, real integrations, general event triggers, multi-agent communication, ROI estimates, and additional agents remain in their specified later phases. Their absence is intentional scope control.

## Tests and native deployment gate

Unit and policy contracts:

```sh
python -m venv .venv
# Activate .venv using the command for your OS.
pip install -r requirements.txt -c requirements.lock
python -m pytest tests/unit -q
```

Security and workflow tests require a **disposable** native PostgreSQL database with pgvector and an isolated Redis DB. Set `TEST_DATABASE_URL` to a migration-owner connection whose database name ends in `_test`, and set `DATABASE_URL`, `AUTH_DATABASE_URL`, `REDIS_URL`, `APP_DB_PASSWORD`, `AUTH_DB_PASSWORD`, `DISPATCH_DB_PASSWORD`, and `SEED_PASSWORD`. The fixture migrates and resets only that test database. Never point the tests at a deployment database or a shared Redis database.

Run `python infrastructure/native_gate.py` on a Docker-capable machine for disposable native tests, a complete cold boot, a high-value approval through the real worker, and a Redis outage/recovery check. `.github/workflows/verify.yml` runs this gate and the frontend build when installed in a GitHub repository. Neither the gate nor hosted CI has run here.

The supplied isolated test stack configures the database services automatically:

```sh
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f docker-compose.test.yml down
```

```sh
python -m pytest -q
cd apps/web
npm ci
npm run build
```

Integration tests skip visibly when no test database is supplied. See the saved verification report for what was actually exercised during this build.

## External resources for the next stage

| Resource | When needed | How it is used |
| --- | --- | --- |
| Docker-capable machine or host | Run/deploy the complete stack and finish native verification | Next.js, FastAPI, PostgreSQL, Redis, and Python workers |
| Chosen LLM provider, model, API endpoint, and key | Enable live intent extraction | Key resolved from Vault, not a prompt or database row |
| Managed Vault/KMS and scoped service identity | Before a hosted deployment with credentials | Replaces the included local dev Vault |
| Hosting account and domain | After native verification | HTTPS, secure cookies, private networking, backups |
| Sandbox payment/ecommerce/helpdesk/email credentials | V2 only | Implement and verify real adapters incrementally |
| Fish Audio credentials | Voice phase only | Behind the VoiceProvider boundary |

The project does not need Gmail, Slack, payment, or voice accounts to run its V1 simulator.

## Repository layout

`apps/` contains the web app and API. `agents/` contains the two workflow definitions. `core/` owns models, policies, tools, execution, security, knowledge, and evaluation. `simulations/` supplies the replaceable Acme backend. `infrastructure/` contains migrations, workers, Docker setup, and local configuration. `tests/` contains policy, workflow, and isolation checks. `docs/` records the requirements, design decisions, verification limits, and next integration work.

## New operational screens

**Reconciliation** lists UNKNOWN receipts. Owners/finance approvers can verify fresh provider evidence or close an unresolved action. Neither option retries a mutation, sends a customer message, or resumes the run. **Model budget** separates recorded run costs from conservative daily reservations; simulation remains explicitly labeled. **Audit log** exports authenticated history, with legacy unsigned rows counted. **Approvals** shows independent-review requirements and security signals.

Read `docs/hardening-status.md`, `docs/operations-runbook.md`, and `docs/product-validation.md` for roadmap coverage and the next concrete decisions.
