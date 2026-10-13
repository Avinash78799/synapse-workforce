-- OAuth tokens, client secrets and PKCE verifiers live only in Vault.
ALTER TABLE vendor_installations ADD CONSTRAINT vendor_installation_scope UNIQUE(tenant_id,agent_id,id);
CREATE TABLE vendor_oauth_connections (
  installation_id uuid PRIMARY KEY REFERENCES vendor_installations(id),
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL,
  generation uuid NOT NULL, pending_generation uuid,
  status text NOT NULL CHECK(status IN ('pending','active','refreshing','reauth_required','revoking','revoked','revocation_unconfirmed')),
  expires_at timestamptz, refresh_expires_at timestamptz, refresh_due_at timestamptz,
  last_error text, updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id),
  FOREIGN KEY(tenant_id,agent_id,installation_id) REFERENCES vendor_installations(tenant_id,agent_id,id)
);
CREATE TABLE vendor_oauth_attempts (
  id uuid PRIMARY KEY, installation_id uuid NOT NULL REFERENCES vendor_installations(id),
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, user_id uuid NOT NULL,
  state_hash text NOT NULL UNIQUE, browser_hash text NOT NULL, session_hash text NOT NULL,
  account text NOT NULL, client_id text NOT NULL, redirect_uri text NOT NULL,
  generation uuid NOT NULL,
  status text NOT NULL CHECK(status IN ('pending','exchanging','completed','failed')),
  expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id),
  FOREIGN KEY(tenant_id,agent_id,installation_id) REFERENCES vendor_installations(tenant_id,agent_id,id)
);
CREATE INDEX oauth_refresh_due ON vendor_oauth_connections(refresh_due_at) WHERE status='active';
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['vendor_oauth_connections','vendor_oauth_attempts'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY runtime_access ON %I TO wf_app USING(true) WITH CHECK(true)',t);
    EXECUTE format('CREATE POLICY scope ON %I AS RESTRICTIVE TO wf_app USING(tenant_id::text=current_setting(''app.tenant_id'',true) AND (current_setting(''app.agent_id'',true)=''control'' OR agent_id::text=current_setting(''app.agent_id'',true))) WITH CHECK(tenant_id::text=current_setting(''app.tenant_id'',true) AND (current_setting(''app.agent_id'',true)=''control'' OR agent_id::text=current_setting(''app.agent_id'',true)))',t);
  END LOOP;
END $$;
GRANT SELECT,INSERT,UPDATE ON vendor_oauth_connections,vendor_oauth_attempts TO wf_app;
-- Scheduler receives only IDs. All work re-enters the ordinary tenant RLS boundary.
CREATE FUNCTION public.due_vendor_oauth() RETURNS TABLE(tenant_id uuid,installation_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT c.tenant_id,c.installation_id FROM public.vendor_oauth_connections c
 WHERE (c.status='active' AND c.refresh_due_at<=now())
 OR (c.status IN ('pending','refreshing') AND c.updated_at<now()-interval '15 minutes')
 OR (c.status='revoking' AND c.updated_at<now()-interval '2 minutes')
 ORDER BY c.updated_at LIMIT 25
$$;
REVOKE ALL ON FUNCTION public.due_vendor_oauth() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.due_vendor_oauth() TO wf_dispatch;
