-- Founder access is provisioned explicitly by the deployment operator. No seed grants.
CREATE TABLE auth_private.platform_founders (
  user_id uuid PRIMARY KEY REFERENCES auth_private.users(id),
  granted_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON auth_private.platform_founders TO wf_auth;
CREATE TABLE tenant_onboarding (
  tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
  items jsonb NOT NULL DEFAULT '{}', notes text NOT NULL DEFAULT '',
  version integer NOT NULL DEFAULT 1,
  updated_by uuid NOT NULL REFERENCES auth_private.users(id),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tenant_onboarding ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_onboarding FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_access ON tenant_onboarding TO wf_app USING(true) WITH CHECK(true);
CREATE POLICY scope ON tenant_onboarding AS RESTRICTIVE TO wf_app
 USING(tenant_id::text=current_setting('app.tenant_id',true))
 WITH CHECK(tenant_id::text=current_setting('app.tenant_id',true));
GRANT SELECT,INSERT,UPDATE ON tenant_onboarding TO wf_app;
CREATE FUNCTION public.founder_workspaces(p_user uuid) RETURNS TABLE(id uuid,name text)
LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
 SELECT t.id,t.name FROM public.tenants t
 WHERE EXISTS(SELECT 1 FROM auth_private.platform_founders f JOIN auth_private.users u ON u.id=f.user_id
              WHERE f.user_id=p_user AND u.active)
 ORDER BY t.created_at DESC LIMIT 200
$$;
REVOKE ALL ON FUNCTION public.founder_workspaces(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.founder_workspaces(uuid) TO wf_auth;
