# Zendesk OAuth implementation and qualification

Status, 26 September 2026: implementation with mocked HTTP/Vault tests. Database lifecycle and concurrency tests are written but not executed in this session. No Zendesk account or real Vault service was used. Gate 2 is not closed. **Sandbox-tested; live business-vendor integrations are not verified.**

## Scope and boundaries

The confidential-client authorization code flow requests only `tickets:read users:read`, with S256 PKCE, one-use state, a short-lived Secure/HttpOnly/Lax browser cookie, and independent revalidation of the original owner session. The main login cookie stays Strict. Account and callback are bound on the server. The callback always returns to the configured HTTPS origin; it accepts no browser-supplied redirect destination.

Migration 009 stores scope-owned connection state and hashes, never access tokens, refresh tokens, client secrets or PKCE verifiers. Composite foreign keys bind installations to their tenant and agent. All secret material uses Vault KV v2 with immutable generation paths and create-only CAS. OAuth never falls back to environment credentials. Refresh runs in a separate worker; an unknown exchange outcome requires reauthorization rather than a blind retry of a potentially consumed rotating grant.

Revocation commits local denial before contacting Zendesk. Only an explicit vendor success confirms token revocation. An unavailable Vault, unknown token or uncertain response leaves `revocation_unconfirmed`; it is not evidence of vendor revocation. Confirmation of a DELETE response does not establish refresh-family behavior without the live-account tests.

## Deployment prerequisites

1. Run the isolated native PostgreSQL/Redis suite using `docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests`. Use only the disposable test stack; its fixtures reset its database. Resolve failures before deployment.
2. Back up the existing database, then apply migrations 009 and 010 using the existing migration service. Earlier migration files are unchanged. Keep OAuth disabled during qualification.
3. Build API, web and the new `oauth-worker`; check Nginx configuration before reload. The API, ordinary worker and OAuth worker must use `APP_ENV=production` in production. The OAuth worker requires the dedicated `wf_dispatch` database role, and re-enters tenant RLS for every job.
4. Provide durable Vault KV v2 at the `secret` mount. Production requires HTTPS with a trusted certificate; the optional Compose dev Vault is ephemeral and is not a production secret store. Grant the service token only the needed tenant integration paths: read/create on `secret/data/...` and delete on `secret/metadata/...`. Audit access. Never use a development root token for customer deployment.
5. Register a Zendesk installation in the intended workspace and support agent. The server returns its UUID credential reference. Provision the **application-owned** confidential OAuth client at `tenants/{tenant}/agents/{agent}/integrations/{reference}/oauth-client` in Vault, containing `client_id` and `client_secret`. Configure its exact redirect as `{WEB_ORIGIN}/api/oauth/zendesk/callback`; `WEB_ORIGIN` must be a bare HTTPS origin.
6. A local client on our own developer account is for that account's testing. Before connecting customers, obtain the applicable app-owned/global OAuth client approval. Do not ask customers to create their own OAuth clients or hand over API credentials for this third-party service.
7. After implementation qualification, set `ZENDESK_OAUTH_ENABLED=true`, recreate the affected services, sign in as workspace owner and authorize. Inspect the sanitized status, run the account read check and a ticket read. These are read-only checks; they do not enable workflow writes.

The internal founder checklist requires separate explicit provisioning in `auth_private.platform_founders` by the database deployment operator. Add only the UUID of the intended active founder account after verifying its identity. Workspace ownership alone never grants founder access; no seed grants are made. Removing that membership removes access on the next API request.

## Refresh, failure and recovery

The scheduler scans due work every five seconds and attempts refresh two minutes before recorded expiry. Successful refresh commits the new Vault generation before deleting the retired generation. Failed cleanup emits a sanitized warning. A failed database scan or job does not advance its healthy heartbeat. Configure alerts on health and `oauth_job_failed`, `oauth_cycle_failed`, and `oauth_retired_secret_cleanup_failed` events before production.

Committed exchange intent prevents duplicate callback/refresh dispatch. A crash during pending exchange or refresh fails closed after fifteen minutes. A bounded external call occurs while holding an installation lock; this deliberately serializes read, refresh and revocation but still requires native contention qualification.

For `reauth_required` or `revocation_unconfirmed`: block platform access, inspect/revoke the grant in Zendesk as an authorized administrator, and reconcile Vault paths before registering a new installation. Do not reset database state to active or copy an old refresh token back into use. The generation and pending-generation metadata identify possible token paths; attempt UUIDs identify verifier paths. A crash or failed Vault cleanup can leave an inactive secret: after confirming vendor revocation and checking active/pending references, an operator must remove orphaned versions. Automated orphan inventory and disaster recovery remain production follow-ups.

Callback query strings contain credentials. The supplied Nginx callback location suppresses access/error request logging; normal access logs omit queries and referrers, and Uvicorn access logging is disabled in the image. Preserve these settings in any additional proxy/APM configuration. Application logs retain sanitized paths and error types. Never paste codes, tokens or Vault values into notes or reports.

## Required evidence before closing gates

- Gate 2: clean migration, real database RLS/foreign-key checks, owner/CSRF denial, expired/replayed/cross-browser state, session revocation, simultaneous callbacks/refresh/revoke, Vault outage, rotating-grant timeout, scheduler restart and revoked-access denial. Native tests are not satisfied by unit mocks.
- Gate 3: the same lifecycle against an actual Zendesk developer/trial account and real Vault, with timestamps, client ownership, granted scopes, refresh/revocation behavior and sanitized evidence. No account is currently available.
- Gate 4: verified vendor-record binding, exact approved mutation, authoritative read-back and uncertain-outcome recovery. This implementation intentionally requests no write scopes.

Per-action automation settings and reversal remain deferred until Gate 4. No vendor-verified or backend-complete claim follows from this implementation.

Primary contracts: [grant and refresh](https://developer.zendesk.com/api-reference/ticketing/oauth/grant_type_tokens/), [PKCE](https://developer.zendesk.com/documentation/ticketing/working-with-oauth/oauth-pkce/), [refresh tokens](https://developer.zendesk.com/documentation/authentication/refresh-token/), [revocation](https://developer.zendesk.com/documentation/authentication/revoking-oauth-token/), [third-party OAuth migration](https://developer.zendesk.com/documentation/authentication/oauth-migration/).
