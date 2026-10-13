-- Read-only vendor inspection is deliberately separate from sandbox execution.
CREATE TABLE vendor_installations (
  id uuid PRIMARY KEY REFERENCES integrations(id),
  tenant_id uuid NOT NULL,
  agent_id uuid NOT NULL,
  vendor text NOT NULL CHECK(vendor IN ('shopify','zendesk')),
  account text NOT NULL,
  secret_ref uuid NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'pending_credentials'
    CHECK(status IN ('pending_credentials','read_check_passed','check_failed','revoked')),
  checked_at timestamptz,
  last_check jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id)
);
CREATE UNIQUE INDEX vendor_active_binding ON vendor_installations(tenant_id,agent_id,vendor,account) WHERE revoked_at IS NULL;
ALTER TABLE vendor_installations ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_installations FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_access ON vendor_installations TO wf_app USING(true) WITH CHECK(true);
CREATE POLICY scope ON vendor_installations AS RESTRICTIVE TO wf_app
  USING(tenant_id::text=current_setting('app.tenant_id',true)
    AND (current_setting('app.agent_id',true)='control' OR agent_id::text=current_setting('app.agent_id',true)))
  WITH CHECK(tenant_id::text=current_setting('app.tenant_id',true)
    AND (current_setting('app.agent_id',true)='control' OR agent_id::text=current_setting('app.agent_id',true)));
GRANT SELECT,INSERT,UPDATE ON vendor_installations TO wf_app;
