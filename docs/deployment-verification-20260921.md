# Local deployment verification — 21 September 2026

**Sandbox-tested; live business-vendor integrations are not verified. The full business-operations backend is not finished.**

## Completed and measured

- Applied migrations 006 (client foundation), 007 (signed custom webhooks) and 008 (vendor installations) to the existing local database. All eight migration entries are present.
- Backed up PostgreSQL before migration and validated the archive table of contents. A restore drill was not performed. Preserved prior built API/web images and backed up modified source files.
- Built backend and Next.js 16.3.5 production Docker images successfully; native Next.js production build also passed.
- Ran the full isolated native PostgreSQL/Redis backend suite: **185 passed, 2 known deprecation warnings**, 128.56 seconds. The warnings remain tracked with a dependency-version review gate; they are not resolved or globally suppressed.
- Upgraded the local API, real worker and dashboard. Nginx serves HTTPS at `https://localhost`; API, worker, database and Redis health checks passed. Web and proxy are running; HTTPS health and dashboard responses were checked separately.
- Executed **11 HTTPS smoke checks**, trusting only the configured local certificate through an SSL context, with certificate verification enabled. Created explicitly fictional verification workspaces, revoked their temporary API keys/webhook endpoints/vendor bindings, and logged out. Verification records remain available in the local database; no customer data or vendor token was used.

- HTTPS certificate-validated health
- Production dashboard served
- Workspace signup and authenticated session
- Three fictional integration categories connected
- Scoped API submission and idempotent retry
- Real worker approval, resumption and verified sandbox outcome
- API key revocation
- Signed webhook intake, retry and revocation
- Accepted webhook processed by real worker
- Vendor registration, missing-credential denial and revocation
- Session logout

Final smoke workspace: `e5f01e08-fd36-4dfb-8594-5f11aee43b2f`. API support run: `bd4adc39-4c51-49df-a691-5d1423546eb6`; 3 sandbox approvals. Distinct webhook Operations run: `9160f2c1-2fe7-4cc8-b1db-471b48bf0a2f`. The initial smoke reused the support case through business-case deduplication; the strengthened final run explicitly required different run IDs.

## Evidence limits and remaining backend work

Browser interaction acceptance is still pending: the browser reported `ERR_CERT_AUTHORITY_INVALID` for the development certificate, and its safety rules require the user to handle that interstitial. Serving the production dashboard over certificate-validated HTTPS is not a browser interaction test.

Shopify and Zendesk registration, Vault references, revocation, account checks and bounded read-only inspection are implemented. Their HTTP contracts were mocked in tests. No live vendor account was accessed. A missing credential failed closed in the deployed API.

The next backend work is vendor OAuth authorization/refresh/revocation, verified customer/order/ticket bindings, policy-governed vendor actions with idempotency and uncertain-outcome reconciliation, and native Shopify/Zendesk webhook contracts. These need actual development accounts for qualification. Public onboarding also needs verified email delivery/password recovery and member deactivation. Production operations need separate backup-restore, monitoring, security and deployment qualification.

Current client-facing wording remains in [client-readiness.md](client-readiness.md). No production-ready, live-integration-verified or compliance-certified claim follows from these sandbox checks.

## Docker Desktop recovery after verification

Docker Desktop subsequently stopped. Its backend startup log reported an inaccessible `dockerInference` runtime socket, matching the failure described in [Docker desktop-feedback issue 531](https://github.com/docker/desktop-feedback/issues/531). This is evidence of the observed local startup failure, not proof of its underlying Windows cause.

Recovery preserved the affected directories under `run.stale-20260921-7fad8e75` and `docker-secrets-engine.stale-20260921-068610eb`. Before renaming, their exact resolved paths and contents were checked: only zero-byte socket reparse points were present. Failed Desktop processes were stopped; Docker restarted successfully. No database volumes, images or application credentials were deleted, and no factory reset was used.

After recovery, all six application services were running. API, worker, PostgreSQL and Redis health checks passed; certificate-validated HTTPS `/api/health` and `/` returned 200. PostgreSQL retained all eight migrations, with three completed and seven escalated runs and no active runs. The disposable `workforce-client-verify` test containers/network were removed. This recovery does not guarantee the Docker Desktop socket issue cannot recur.
