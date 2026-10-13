-- Server-to-server sandbox event intake; no vendor-specific verification implied.
CREATE TABLE auth_private.webhook_endpoints (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  agent_id uuid NOT NULL,
  created_by uuid NOT NULL REFERENCES auth_private.users(id),
  name text NOT NULL,
  generation uuid NOT NULL,
  master_key_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  rotated_at timestamptz,
  revoked_at timestamptz,
  FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id)
);
CREATE INDEX webhook_endpoints_tenant ON auth_private.webhook_endpoints(tenant_id,created_at DESC);
GRANT SELECT,INSERT,UPDATE ON auth_private.webhook_endpoints TO wf_auth;

CREATE TABLE webhook_events (
  tenant_id uuid NOT NULL,
  agent_id uuid NOT NULL,
  endpoint_id uuid NOT NULL REFERENCES auth_private.webhook_endpoints(id),
  event_id uuid NOT NULL,
  event_type text NOT NULL CHECK(event_type IN ('support.duplicate_charge','operations.review')),
  payload_digest text NOT NULL,
  outcome text NOT NULL CHECK(outcome IN ('accepted','rejected')),
  reason_code text,
  run_id uuid,
  attempts integer NOT NULL DEFAULT 1,
  received_at timestamptz NOT NULL DEFAULT now(),
  last_received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,endpoint_id,event_id),
  FOREIGN KEY(tenant_id,agent_id,run_id) REFERENCES agent_runs(tenant_id,agent_id,id),
  CHECK((outcome='accepted' AND run_id IS NOT NULL AND reason_code IS NULL)
     OR (outcome='rejected' AND run_id IS NULL AND reason_code IS NOT NULL))
);
CREATE INDEX webhook_events_recent ON webhook_events(tenant_id,endpoint_id,last_received_at DESC);
ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_events FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_access ON webhook_events TO wf_app USING(true) WITH CHECK(true);
CREATE POLICY tenant_scope ON webhook_events AS RESTRICTIVE TO wf_app
  USING(tenant_id::text=current_setting('app.tenant_id',true))
  WITH CHECK(tenant_id::text=current_setting('app.tenant_id',true));
CREATE POLICY agent_scope ON webhook_events AS RESTRICTIVE TO wf_app
  USING(current_setting('app.agent_id',true)='control' OR agent_id::text=current_setting('app.agent_id',true))
  WITH CHECK(current_setting('app.agent_id',true)='control' OR agent_id::text=current_setting('app.agent_id',true));
GRANT SELECT,INSERT,UPDATE ON webhook_events TO wf_app;
