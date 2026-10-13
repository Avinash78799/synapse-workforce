# Business integration milestones

The user delegated vendor selection. First targets: **Shopify for ecommerce, Zendesk for helpdesk**, and the platform API/signed custom-backend protocol for custom websites/apps. This is an implementation-priority decision, not a claim that either vendor is connected.

| Order | Work | Why / completion evidence |
| --- | --- | --- |
| 1 | Signed custom-backend intake, endpoint controls and event history | Gives business systems a bounded event-to-workflow path, with tenant binding, replay protection and traceable queued work. Implemented in the current sandbox milestone; see signed-webhooks.md. |
| 2 | Per-tenant vendor installations and protected credential references | Shopify/Zendesk accounts must belong to one workspace with minimal scopes; platform-global connector credentials are insufficient. Read-only registration, revocation, credential references and isolation tests implemented; OAuth lifecycle remains. See vendor-connections.md. |
| 3 | Shopify adapter | Read-only order/transaction inspection implemented and mock-tested; test-store qualification remains. Qualify approved mutations only after object mapping, consent, currency, idempotency and uncertain-outcome behavior pass tests. |
| 4 | Zendesk adapter | Read-only ticket metadata inspection implemented and mock-tested. Next, link tickets to verified customer/order context; add controlled internal notes/status changes and read-back. Do not equate a successful HTTP request with a verified support outcome. |
| 5 | Native vendor webhooks | Implement each vendor's actual signature, subscription and retry contracts, map trusted installation identity to the workspace, and translate validated events into the common intake/runtime. The custom HMAC endpoint is not a drop-in vendor webhook receiver. |
| 6 | End-to-end qualification and client onboarding | Test on an actual Shopify dev store and Zendesk development/trial account, then qualify Docker/browser flows in the current implementation task. Record vendor/environment/date/scenarios; retain sandbox qualifications for everything not exercised. |

Shopify documents development stores for app testing: https://shopify.dev/docs/apps/build/stores/development-stores

Zendesk documents trial and sponsored development accounts: https://developer.zendesk.com/documentation/api-basics/getting-started/getting-a-trial-or-sponsored-account-for-development/

Zendesk sponsored-account eligibility is conditional; do not assume permanent free access. Account creation and credentials will be needed for vendor-backed verification, but no clients, purchases or real customer data are required for the current local sandbox work. No vendor account was created in this milestone.

After the first external test-account workflow is qualified, prioritize verified email/password recovery and member deactivation before public onboarding; then an authenticated website widget. These are separate from the signed backend webhook contract.
