# Operations handoff — prepared, not deployed

The simulator has no live payment adapter. This runbook prepares the next deployment; no monitoring service, scheduled backup, production pipeline, or incident response team has been activated.

## Release gate

Run `python infrastructure/native_gate.py` from a Docker-capable machine. It creates temporary source/configuration, random isolated Compose project names and local port, runs native tests, boots the complete stack, times startup, executes an independently approved refund through the real worker, and tests a Redis outage with queued work. It removes only its own disposable stacks/volumes. Reports go to `artifacts/native-gate.json` and `native-gate.log`.

The GitHub Actions definition also runs the Next.js build and retains gate evidence. It performs no production deployment. Add staging/production deployment jobs only after the hosting target, secrets, and release controls are chosen.

The gate must pass before accepting a release. Prepared tests are not passing evidence. Follow it with actual provider usage/billing tests, a restore exercise, an independent security review, and supervised pilot acceptance.

## Service health and initial alert signals

- `/api/health` checks database and Redis reachability; Compose uses it for API health.
- Worker health uses a heartbeat file updated during queue cycles and supervised work. It indicates local liveness, not successful business outcomes.
- API logs include request ID, method, path, status, and elapsed milliseconds. Logs exclude request bodies/credentials. Worker failures log run ID and error type.
- Configure alerts on sustained API failures/latency, unhealthy workers, unpublished/old outbox rows, oldest pending Redis jobs, UNKNOWN receipts, overdue approvals, repeated approval security signals, and exhausted/uncertain model budgets.
- Select alert thresholds from staging measurements. No availability SLA, production throughput, or load target has been measured.

## Incorrect or uncertain financial action

1. Hard-abort the affected agent and record an incident reason. Restrict any relevant external credential in the provider's console if compromise is suspected.
2. Preserve run/task IDs, the persisted idempotency key, receipts, policy/approval records, timestamps, and a verified audit export. Retain a copy outside the application database.
3. Inspect the authoritative provider ledger by the exact payment and idempotency key. A timeout or incomplete response is not evidence that a refund failed.
4. Use Reconciliation to verify existing evidence or close unresolved. This screen never retries the mutation, sends a message, unlocks the agent, or resumes the run. If evidence is missing, escalate to the provider/business owner.
5. Have an authorized independent reviewer approve any new corrective financial action through the eventual real integration's established process. Do not edit database rows to manufacture success.
6. Rotate/revoke credentials when appropriate, document the cause and customer impact, and complete the relevant regression tests. Owner password is required to unlock the hard-aborted agent; old execution epochs remain invalid.
7. Notify affected customers through an authorized channel under the organization's incident/privacy procedure. No notification is automatically sent by this project.

## Queue or Redis outage

Preserve PostgreSQL and Redis data. Outbox publication is at-least-once, so delivery may repeat after recovery. Inspect receipts before trying to replay work. Restore Redis/worker connectivity, inspect dead letters and UNKNOWN actions, and verify business state. Never clear receipt, case, or idempotency records to force execution.

## Audit integrity failure

Stop exporting history as verified if chain verification fails. Preserve the suspect database snapshot and previous independent exports. Compare using the offline verifier and retained keys. An externally retained checkpoint is essential for detecting tail truncation. Legacy unsigned events remain explicitly unverified. A compromised signing key requires an incident response; HMAC cannot distinguish the real signer from an attacker holding that key.

## Backup and restore exercise

Choose encrypted storage, retention, access ownership, and recovery objectives for the actual host. Use provider-managed backups or `pg_dump` with a scoped backup identity. Keep audit keys and old verification keys backed up separately with restricted access. Preserve the Redis persistence strategy and secrets-provider recovery configuration too.

Restore into a new isolated database and Redis instance, never over a live tenant database for testing. Restore the schema, scoped data, receipts, approvals, outbox, and audit records; provision the dedicated runtime roles with new credentials. Verify migrations/checksums, RLS login tests, audit signatures against a retained checkpoint, idempotency records, and application read-only access before enabling workers. Inspect pending jobs/approvals before releasing any live adapter. Record restore duration and actual data loss against the agreed objectives. This exercise has not yet been executed.

## Public-hosting prerequisites

Use real separate user identities and passwords; local demo accounts intentionally share a simulation password. Configure HTTPS, exact origin, secure cookies, private service networking, managed secrets, scoped identities, backups, monitoring, retention rules, and an operational owner. Obtain relevant legal/privacy/payment reviews from qualified reviewers before collecting customer data or handling money. No compliance certification is implied by this runbook.
