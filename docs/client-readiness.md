# Client-facing readiness statement

**Status as of 26 September 2026: sandbox-tested; live business-vendor integrations are not verified.**

Use this paragraph together whenever describing the current product to a prospective client:

> We are building a founder-managed Zendesk support ticket triage service for small ecommerce businesses. The current demonstration uses fictional local cases and shows approval controls and recorded outcomes. Read-only OAuth and trust features are implemented but have not been qualified against a live Zendesk account. No live ticket action, payment mutation, real email delivery or customer outcome has been verified. These results do not establish production readiness or compliance certification.

## Evidence boundaries

| Capability | Evidence in this milestone | Claim permitted |
| --- | --- | --- |
| Workspace signup, invitations, scoped API keys, tenant isolation and retry behavior | Automated tests against disposable native PostgreSQL and Redis | Sandbox-tested access and workflow controls |
| Signed custom-backend event intake | HMAC/expiry/duplicate/isolation and transactional queue tests against local sandbox workflows | Sandbox-tested custom protocol; not native Shopify/Zendesk webhook verification |
| Vendor registration and record inspection | Mocked Shopify/Zendesk HTTP contracts, scoped PostgreSQL integration tests | Read-only implementation tested locally; no live vendor verification |
| Local deployed API and worker | Certificate-validated HTTPS smoke tests through Docker/Nginx, including approvals and a distinct webhook-triggered Operations job | Historical local sandbox deployment and trusted-browser interactions verified; new OAuth/trust changes are not deployed or browser-qualified |
| Three integration categories | Local simulator adapters and fictional records | Internal regression coverage; the current demo offer is Zendesk-style ticket triage only |
| LLM provider connections from earlier work | Separate historical provider diagnostics, not business-system tests | Only describe the specific provider test and its date; it does not verify Shopify, CRM, payment or email connectivity |
| Live ecommerce/helpdesk/custom-site accounts, external payment writes and real email delivery | Not exercised in this milestone | Not vendor-verified |
| Production customer deployment or compliance | Not established by these tests | No production-ready, certified, client-success or ROI claim |

Every proposal, demo summary, release summary or generated sales report should include the bold status and the paragraph above next to any test counts. Do not shorten “sandbox-tested” to “verified integrations”. Preserve the qualification when copying or summarizing older engineering reports. Internal test counts belong in the engineering evidence, not as proof of real customer outcomes.

To change a vendor's status, record the exact adapter/version, vendor and test-account environment, date, credential scopes, executed scenarios, signed webhook/replay checks where relevant, tenant-isolation results, approval behavior, authoritative read-back and failure recovery. A vendor test account establishes verification in that test environment only. Production qualification requires separate deployment and operational evidence. Until evidence exists, keep that vendor marked unverified.

The 26 September source milestone has 129 passed and 105 skipped tests; it is not a replacement for native/live qualification. See [current evidence](trust-milestone-20260926.md). Customer-facing summaries must retain the qualification above.
