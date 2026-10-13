# Trust and Zendesk implementation — 26 September 2026

**Sandbox-tested; live business-vendor integrations are not verified.** Source implementation is not deployment or backend completion.

## Implemented

- Zendesk read-only OAuth authorization, state/PKCE, server bindings, Vault-only credentials, scheduled rotating refresh, local-first revocation and fail-closed recovery. Disabled by default. See [setup and limits](zendesk-oauth.md).
- Trust Dashboard reads existing audit logs, tasks, approvals and receipts. It shows intentions, recorded policy reasons, permission decisions, current outcome, timestamps and pending approvals without raw JSON. An unknown receipt overrides a completed-task label. Missing evidence is labeled explicitly. Reversal remains unverified; no undo button is shown.
- Internal founder onboarding: all fifteen requested items, three status choices, dates, per-item notes and workspace notes. Server-side founder membership, tenant scope and version conflicts protect access and updates. Notes never enter the ordinary tenant audit feed. Live-action and per-action-automation milestones cannot be marked Done while those capabilities remain unqualified.
- The fresh Acme seed now demonstrates one payment-related support ticket triage workflow: inspect fictional records, request approval, escalate to a person. No refund or customer message is executed. Broader scenarios remain explicit regression fixtures; existing seeded workspaces are preserved unchanged.

## Schema coverage

Trust Dashboard adds no table. Existing task reason/decision, audit policy/version/time, approval and receipt records cover the current view. The schema does not establish vendor reversibility or an immutable historical connector identity, so the UI does not invent them.

New migration 009 adds OAuth connection/attempt metadata with RLS and a limited scheduler discovery function. New migration 010 adds founder membership and per-tenant checklist state with RLS and optimistic versioning. Those records did not exist in the previous schema. Migrations 001–008 are untouched.

## Verification and unresolved work

- Trusted HTTPS Gate 1 previously passed on 25 September without bypass: sign-in, dashboard, approvals, vendor screen and sign-out. See [saved evidence](evidence/trusted-https-browser-verification.json). This verifies the previously deployed UI.
- Current Python suite: **129 passed, 105 skipped, 2 warnings**. Skipped tests require the isolated PostgreSQL/Redis environment; new lifecycle, concurrency, tenant-isolation and default-demo integration tests have not run. The historical 185-pass native result does not qualify these changes.
- TypeScript check passed. Next.js compiled successfully, then the production build failed with Windows `spawn EPERM` starting its TypeScript worker. A separate TypeScript run passed. Full production build and browser acceptance of these new screens remain pending.
- `npm ci` completed. This attempt's build failure was process creation, not registry download. The two known deprecations remain visible under the existing dependency-version gate; no warning filter was relaxed.
- Docker named-pipe access is denied in this terminal session. No migrations, Nginx reload, container rebuild or new deployed browser test was performed.
- No real Zendesk account, real Vault lifecycle, vendor action, email delivery, restore drill or production operational qualification was exercised.

Next: run the isolated database tests and complete the build/deployed browser checks to qualify Gate 2 implementation; obtain a Zendesk developer account for Gate 3; then implement and qualify controlled Zendesk writes at Gate 4. Automation settings, reversals and Shopify remain sequenced afterward. Production monitoring, security review, onboarding email and restore testing also remain open.
