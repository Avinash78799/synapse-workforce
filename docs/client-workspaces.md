# Client workspaces and sandbox access

**Sandbox-tested; live business-vendor integrations are not verified.** Any prospective-client summary must preserve the qualification in [client-readiness.md](client-readiness.md). Dependency and build follow-ups are tracked in [verification-followups.md](verification-followups.md).

This milestone adds a repeatable onboarding path for businesses before live customer integrations are available. The three categories are ecommerce, custom websites/apps, and CRM/helpdesk. Each category currently uses fictional local records, with the existing worker, policies, approval checks and verified tool receipts.

## Start a workspace

1. Open the dashboard, sign out of the original demo if necessary, and choose **Create a business workspace**.
2. Enter the business name, owner details, a password of at least 12 characters, and the primary category. All three categories remain available.
3. Sign in. The workspace has two agents at autonomy level 3 and no customer records.
4. Open **Integrations**, confirm loading fictional data, and connect all three sandboxes. Test each connection.
5. Start the support demo. Review the proposed mutations in **Approvals**. Invite a separate finance approver when independent approval is required; a requesting owner cannot approve their own action.

Signup defaults to enabled in development and disabled in the production Compose override. `WORKSPACE_SIGNUP_ENABLED=true` explicitly enables it. Public signup has no email verification or account recovery yet, so keep production signup disabled until those are implemented. Existing demo tenants are preserved and do not acquire a sandbox profile automatically.

## Team access

The owner can invite operators, finance approvers, and viewers from **Team**. Invitation links contain a random secret in the URL fragment, are valid for 48 hours and can be accepted once. The browser clears the fragment on loading. Share the link manually; this milestone does not send email. Open the link while signed out. A user email currently belongs to one workspace. Invitation revocation prevents acceptance; it does not deactivate an already accepted member.

## Backend integration

From **Developer access**, issue an expiring key bound to this workspace, selected agents and the needed scopes: `runs:write`, `runs:read`, `integrations:read`. The raw secret appears once; only its hash is stored. Rotation revokes the old key atomically and preserves permissions and expiry. Revocation blocks future authentication; it does not cancel already accepted jobs. Keys cannot manage the workspace or approve actions. Keep them on a backend server, never in browser/mobile code.

Send `Authorization: Bearer <key>` without browser cookies or an Origin header:

```http
POST /api/v1/runs
Content-Type: application/json

{
  "agent_id": "<support agent UUID>",
  "request_key": "<new UUID>",
  "objective": "Investigate the duplicate charge",
  "customer_id": "CUS-001",
  "order_id": "ORD-1001"
}
```

The response is HTTP 202 with `run_id`, `status_url` and `replayed`. Poll `GET /api/v1/runs/{run_id}` for status and bounded task outcomes. Repeat the identical request key and payload after a timeout; a changed payload with the same key returns 409. Existing business-case deduplication also prevents repeating a support refund under a new request key. `GET /api/v1/integrations` reports sandbox readiness. Per-key request limits and the shared per-agent hourly submission limit apply; safe replays bypass the submission limit but still count toward the key request limit.

## Runtime boundaries

Managed sandbox workspaces always use simulation models, embeddings and connectors, including when deployment defaults select external providers. Disconnecting a category blocks subsequent dispatch through that connector and new complete workflows, while retaining records and receipts. It does not reverse completed actions. Connection tests inspect local data and do not verify vendor credentials.

Migration `006_client_foundation.sql` adds tenant-scoped workspace metadata, connections, request deduplication, hashed keys and invitations. Provisioning and invitation acceptance use narrow transactional database functions; application roles do not gain general user or tenant write access. Do not edit applied migration checksums.

## Deployment and remaining scope

Build the API, worker and web from the same revision, apply migrations with the migration-owner job before starting the new application code, and keep the existing runtime database roles. Take a database backup before updating an existing deployment. No data reset or reseed is required for this upgrade. Runtime service keys are different from LLM vendor API keys.

Real Shopify/WooCommerce adapters, CRM vendor OAuth, native vendor webhooks, an embeddable customer widget, billing, email verification/recovery, member removal and multi-workspace identities are not implemented here. The next product milestone should qualify one live test-store connector and one helpdesk test-account connector with tenant-scoped secrets, least-privilege scopes, idempotent writes and authoritative read-back. No real client account is needed to test the current sandbox milestone.

## Incoming custom-backend events

The Webhooks screen now supports signed sandbox events, owner-controlled endpoints and recent delivery history. See [signed-webhooks.md](signed-webhooks.md) for setup and the exact wire contract. This does not implement the native Shopify or Zendesk webhook protocols. The next vendor targets and rationale are tracked in [next-integration-milestones.md](next-integration-milestones.md).
