# Backend completion plan — 26 September 2026

Current offer: founder-managed Zendesk support ticket triage for small ecommerce businesses. Zendesk comes first; Shopify and broader workflows remain later gates. Follow Phase 1 in order; preparation is not evidence that a stage passed.

## Corrected baseline

- Last complete verification: 185 passing backend tests, 11 local HTTPS checks, successful Docker and native frontend builds. These are historical results, not verification of subsequent changes.
- Migrations 001–008 applied. Database archive created and inspected; restore not tested.
- Vendor registration, scoped Vault references, revocation and read-only clients exist. Shopify/Zendesk HTTP tests used mocks; no live account qualification.
- Core action receipts, idempotency, uncertain-outcome reconciliation, tenant audit, model usage accounting, scoped rate limits and an approve/reject UI exist. Extending and qualifying these for vendor operations remains necessary.
- Read-only Zendesk OAuth implementation and trust surfaces added; see [current evidence](trust-milestone-20260926.md). New migrations 009–010 are not applied or native-tested. No production operational qualification.

## Phase 1 gates

| Order | Work | Completion evidence | Current status |
| --- | --- | --- | --- |
| 1 | Trusted local HTTPS | Windows trusts the development CA, Nginx serves its leaf certificate, browser interaction tests pass without certificate bypass | PASSED 25 September: trusted HTTPS and real browser sign-in/navigation/sign-out without bypass; [evidence](evidence/trusted-https-browser-verification.json) |
| 2 | Zendesk OAuth | Authorization code + state/PKCE, server-owned account/redirect binding, Vault-only token storage, scheduled refresh, revocation and failure/concurrency tests | Implemented with unit/mocked tests; native database, real Vault, deployment and new browser qualification pending. NOT CLOSED |
| 3 | Live Zendesk account | Real authorization, expiry/refresh, missing-credential and revoked-access tests with dated evidence | BLOCKED: no Zendesk account yet; user requested implementation and mock tests meanwhile |
| 4 | Controlled Zendesk actions | Verified record binding, exact approval, operation identity, read-back and recovery after ambiguous timeouts | Pending vendor implementation |
| 5 | Shopify equivalents | Repeat authorization, lifecycle and action qualification against actual Shopify contracts | Pending first vendor gates |
| 6 | Restore drill | Restore into a scratch database and verify schema, scoped access and recorded state | Pending |
| 7 | Cross-vendor bindings | Verified customer/order/ticket links and adversarial cross-account tests | Pending; individual action bindings must already be enforced at step 4 |

Zendesk goes first to exercise controlled support updates before payment-related operations. Shopify is not being implemented in parallel. A mock OAuth test cannot close the live-account gate.

## Phase 2

Extend existing input/policy controls to vendor text; classify retries and add circuit breakers; qualify existing tenant/key limits; review token rotation and security. Extend existing audit and cost records with vendor operation identity and error tracking. Finish verified email/password recovery and member deactivation. Add monitoring, alerting, deployment/restoration runbooks and model-provider qualification. Test and improve the existing approval UI rather than rebuilding it as a missing feature.

## Current HTTPS setup

`infrastructure/configure_trusted_https.ps1` is prepared for an interactive Windows terminal with Docker access. It checks Docker before changing trust, installs the mkcert CA, checks trust-store membership, backs up the old certificate/key, reloads Nginx, and verifies HTTPS. Failure restores the previous leaf pair. The CA key stays outside the directory mounted into Nginx.

The generated local CA thumbprint is `7C6EEF871EB8606C8B6CCABA3D7FEC71D860ABEB`. mkcert v1.4.4 was downloaded through verified HTTPS from its official GitHub release. SHA256 of the downloaded Windows binary: `d2660b50a9ed59eada480750561c96abc2ed4c9a38c6a24d93e30e0977631398` (recorded download hash, not a separately published checksum).

The deployed site subsequently passed trusted HTTPS and browser interaction verification on 25 September. The current terminal session still cannot access the Docker Windows named pipe, so it has not deployed the new OAuth/trust changes. This does not reopen the completed HTTPS gate. Do not bypass certificate warnings.

mkcert is for development only. Never share or commit its CA private key. Official references: [mkcert](https://github.com/FiloSottile/mkcert), [Zendesk OAuth grant and refresh contracts](https://developer.zendesk.com/api-reference/ticketing/oauth/grant_type_tokens/), [token revocation](https://developer.zendesk.com/documentation/authentication/revoking-oauth-token/).

**Sandbox-tested; live business-vendor integrations are not verified.**

## Trust additions and sequencing

Trust Dashboard reuses existing audit/task/approval/receipt data; no new trust table. Migration 010 adds founder-only onboarding state and explicit founder membership because neither existed. All fifteen checklist items, dates and notes are implemented; unqualified live milestones cannot be marked Done. The fresh demo is one human-approved ticket-triage workflow; old records are preserved.

Per-action automation configuration and genuine vendor reversal remain **after Gate 4**. No undo or greater autonomy is promised. See [OAuth setup](zendesk-oauth.md) and [current evidence](trust-milestone-20260926.md).
