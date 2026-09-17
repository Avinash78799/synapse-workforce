# Live model and deployment handoff

## Enable a live model only after local verification

The default `MODEL_PROVIDER=simulation` is deliberate. It runs deterministic business workflows without paid model calls. A live model currently classifies the objective and extracts typed metadata; it does not choose arbitrary tools or authorize financial actions.

To enable the existing adapter, configure:

| Setting | Required value |
| --- | --- |
| `MODEL_PROVIDER` | `chat_completions` |
| `LLM_BASE_URL` | Your provider's HTTPS API base, before `/chat/completions` |
| `LLM_MODEL` | An explicit supported model name |
| `LLM_INPUT_USD_PER_MILLION` | The model's current input-token price |
| `LLM_OUTPUT_USD_PER_MILLION` | The model's current output-token price |
| `DAILY_MODEL_BUDGET_USD` | A positive per-tenant daily budget; default $5 |
| `LLM_SECRET_REF` | Vault KV v2 path relative to the `secret` mount; default `platform/llm` |
| `VAULT_ADDR` | The accessible Vault URL |
| `VAULT_TOKEN_FILE` | Mounted scoped service-token file |

Vault KV v2 should contain a `value` field at the selected path. The API key is never placed in a business table or client bundle. The default local Vault profile can be started with `docker compose --profile model up -d vault` for manual sandbox configuration. It loses secrets on restart and is not suitable for deployment.

The adapter expects chat-completions JSON mode and `prompt_tokens`/`completion_tokens` usage fields. Providers that do not support these fields fail closed. Input scope, target entity, intent, and schema are validated. Output is limited to 500 tokens. Dollar budgeting reserves a conservative input/output allowance using configured prices before a request; ambiguous requests retain the reservation. Provider tokenization, usage reconciliation, prices, and behavior require qualification against the chosen service before relying on the dollar limit for production.

Rebuild/restart the API and worker after changing model configuration. The platform must still show “simulated business actions”: enabling a model does not enable real refunds or communication.

## Deployment requirements

Use a host that can run the complete stack: Next.js, FastAPI, PostgreSQL with pgvector, Redis, and Python worker processes. A static-only host is insufficient. The supplied Compose configuration binds the web port only to localhost for local development and does not expose database, Redis, or API ports.

Before a public deployment:

1. Pass the native Docker test suite and exercise worker restart, duplicate delivery, expiry wakeups, graceful drain, hard abort, and unknown-state reconciliation.
2. Replace demo accounts/passwords with deliberate user provisioning and organizational role assignments.
3. Set up an HTTPS reverse proxy, the exact `WEB_ORIGIN`, and `SECURE_COOKIES=true`.
4. Replace development Vault with managed Vault/KMS, scoped renewable identities, appropriate TLS, and credential rotation.
5. Add backups and restore verification, service health alerts, private networking, retention rules, and operational ownership.
6. Configure the selected model, prices, and budget; run an evaluation dataset that measures task correctness, tool selection, escalation, and business outcomes.

## Real integrations stay in V2

Start with a single sandbox adapter and keep the same core tool contract. Its mutation path must send the persisted idempotency key to the provider, expose a reliable status lookup, verify exact entity/amount/currency/state, and distinguish **confirmed**, **absent/failed**, and **unknown** outcomes.

For payment, email, helpdesk, or commerce accounts, supply sandbox credentials through the secrets manager. Add signed-webhook verification, entity deduplication, retries with known failure semantics, and approval notification delivery before any production rollout. Never equate a recorded simulation message with a sent email or an accepted HTTP request with a verified refund.

Fish Audio, general external-event automation, and MCP/A2A are later roadmap phases, not prerequisites for this V1 build.


## V1.1 audit keys and upgrade

Generate the local audit key with `python infrastructure/configure_audit.py` before upgrading an existing stack. New installations do this inside `configure_local.py`. API and worker mount `/run/secrets/audit_signing_key`; loss of this key prevents verification of its historical signatures. Audit generation fails closed if the key is unavailable or too short. The app startup checks the key as well as the dedicated database roles.

Download an audit export as an owner/finance approver, then verify it offline:

```sh
python -m infrastructure.verify_audit workforce-audit.json --key-file .secrets/audit_signing_key
```

To detect truncation against previously retained history, add `--previous previous-export.json`. Retain exports/checkpoints independently of PostgreSQL, preferably in a write-once destination under separate administration. The project does not configure that destination without a hosting choice.

For key rotation, retain previous verification keys, mount them read-only, and set `AUDIT_VERIFY_KEY_FILES` to their colon-separated paths in the API environment before exporting historical chains. The offline verifier accepts repeated `--key-file` arguments. Validate an export using both old and new keys before retiring the previous signing key. Protect verification keys as signing-capable secrets: this implementation uses symmetric HMAC.

V1.1 removes the integration secret cache. Configure managed Vault identities, access policy, TLS, rotation, and revocation when the provider is selected. Merely running local dev Vault does not complete this deployment task.
