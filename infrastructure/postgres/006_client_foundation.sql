-- Client onboarding and developer access. Existing tenants are left unchanged.
CREATE TABLE workspace_profiles (
  tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
  category text NOT NULL CHECK(category IN ('ecommerce','custom','crm')),
  mode text NOT NULL DEFAULT 'sandbox' CHECK(mode='sandbox'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE workspace_connections (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
  category text NOT NULL CHECK(category IN ('ecommerce','custom','crm')),
  provider text NOT NULL CHECK(provider IN ('sandbox_commerce','sandbox_custom','sandbox_helpdesk')),
  status text NOT NULL DEFAULT 'disconnected' CHECK(status IN ('connected','disconnected')),
  checked_at timestamptz, check_result jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,category)
);
CREATE TABLE api_submissions (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, request_key uuid NOT NULL,
  api_key_id uuid NOT NULL, payload_digest text NOT NULL, run_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,agent_id,request_key),
  FOREIGN KEY(tenant_id,agent_id,run_id) REFERENCES agent_runs(tenant_id,agent_id,id)
);
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['workspace_profiles','workspace_connections','api_submissions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY runtime_access ON %I TO wf_app USING(true) WITH CHECK(true)',t);
    EXECUTE format('CREATE POLICY tenant_scope ON %I AS RESTRICTIVE TO wf_app USING(tenant_id::text=current_setting(''app.tenant_id'',true)) WITH CHECK(tenant_id::text=current_setting(''app.tenant_id'',true))',t);
  END LOOP;
END $$;
GRANT SELECT ON workspace_profiles TO wf_app;
GRANT SELECT,UPDATE ON workspace_connections TO wf_app;
GRANT SELECT,INSERT ON api_submissions TO wf_app;

CREATE TABLE auth_private.client_api_keys (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
  issued_by uuid NOT NULL REFERENCES auth_private.users(id), name text NOT NULL,
  token_hash text NOT NULL UNIQUE, prefix text NOT NULL,
  scopes text[] NOT NULL, agent_ids uuid[] NOT NULL, rate_per_minute integer NOT NULL CHECK(rate_per_minute BETWEEN 1 AND 300),
  created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
  revoked_at timestamptz, last_used_at timestamptz,
  CHECK(cardinality(scopes)>0), CHECK(cardinality(agent_ids)>0)
);
CREATE INDEX client_keys_tenant ON auth_private.client_api_keys(tenant_id,created_at DESC);
GRANT SELECT,INSERT,UPDATE ON auth_private.client_api_keys TO wf_auth;

CREATE TABLE auth_private.workspace_invitations (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
  invited_by uuid NOT NULL REFERENCES auth_private.users(id), email text NOT NULL,
  role text NOT NULL CHECK(role IN ('operator','finance_approver','viewer')),
  token_hash text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL, accepted_at timestamptz, revoked_at timestamptz
);
GRANT SELECT,INSERT,UPDATE ON auth_private.workspace_invitations TO wf_auth;

-- Narrow, atomic provisioning: runtime roles do not gain general tenant/user writes.
CREATE FUNCTION auth_private.provision_workspace(p_tenant uuid,p_owner uuid,p_business text,p_email text,p_name text,p_password text,p_category text,p_agents jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,auth_private AS $$
DECLARE a jsonb; skill text; tool jsonb; category text;
BEGIN
  IF p_category NOT IN ('ecommerce','custom','crm') OR jsonb_array_length(p_agents)<>2 THEN
    RAISE EXCEPTION 'Invalid workspace definition';
  END IF;
  INSERT INTO public.tenants(id,name) VALUES(p_tenant,p_business);
  INSERT INTO auth_private.users(id,tenant_id,email,name,password_hash,role) VALUES(p_owner,p_tenant,lower(p_email),p_name,p_password,'owner');
  INSERT INTO public.workspace_profiles(tenant_id,category) VALUES(p_tenant,p_category);
  FOREACH category IN ARRAY ARRAY['ecommerce','custom','crm'] LOOP
    INSERT INTO public.workspace_connections(id,tenant_id,category,provider)
      VALUES(gen_random_uuid(),p_tenant,category,CASE category WHEN 'ecommerce' THEN 'sandbox_commerce' WHEN 'custom' THEN 'sandbox_custom' ELSE 'sandbox_helpdesk' END);
  END LOOP;
  FOR a IN SELECT value FROM jsonb_array_elements(p_agents) LOOP
    INSERT INTO public.agents(id,tenant_id,kind,name,owner_name,autonomy,model_tier)
      VALUES((a->>'id')::uuid,p_tenant,a->>'kind',a->>'name',p_name,3,'balanced');
    INSERT INTO public.agent_policies(tenant_id,agent_id,version,rules,created_by)
      VALUES(p_tenant,(a->>'id')::uuid,1,a->'rules','workspace_onboarding');
    INSERT INTO public.agent_versions(tenant_id,agent_id,version,definition)
      VALUES(p_tenant,(a->>'id')::uuid,'1.0.0',jsonb_build_object('kind',a->>'kind','environment','sandbox','source','client_onboarding'));
    FOR skill IN SELECT jsonb_array_elements_text(a->'skills') LOOP
      INSERT INTO public.agent_skills(tenant_id,agent_id,name) VALUES(p_tenant,(a->>'id')::uuid,skill);
    END LOOP;
    FOR tool IN SELECT value FROM jsonb_array_elements(a->'tools') LOOP
      INSERT INTO public.agent_tools(tenant_id,agent_id,name) VALUES(p_tenant,(a->>'id')::uuid,tool->>'name');
      INSERT INTO public.agent_permissions(tenant_id,agent_id,action,allowed) VALUES(p_tenant,(a->>'id')::uuid,tool->>'name',true);
    END LOOP;
    INSERT INTO public.workflow_definitions(tenant_id,agent_id,name,version,definition)
      VALUES(p_tenant,(a->>'id')::uuid,a->>'kind','1.0',jsonb_build_object('verify_every_mutation',true,'environment','sandbox'));
  END LOOP;
  RETURN p_tenant;
END $$;
REVOKE ALL ON FUNCTION auth_private.provision_workspace(uuid,uuid,text,text,text,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_private.provision_workspace(uuid,uuid,text,text,text,text,text,jsonb) TO wf_auth;

CREATE FUNCTION auth_private.accept_workspace_invitation(p_hash text,p_name text,p_password text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,auth_private AS $$
DECLARE invitation auth_private.workspace_invitations; new_user uuid;
BEGIN
  SELECT * INTO invitation FROM auth_private.workspace_invitations WHERE token_hash=p_hash AND revoked_at IS NULL AND accepted_at IS NULL AND expires_at>now() FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM auth_private.users WHERE id=invitation.invited_by AND tenant_id=invitation.tenant_id AND role='owner' AND active) THEN
    RAISE EXCEPTION 'Invitation unavailable' USING ERRCODE='P0001';
  END IF;
  new_user=gen_random_uuid();
  INSERT INTO auth_private.users(id,tenant_id,email,name,password_hash,role) VALUES(new_user,invitation.tenant_id,invitation.email,p_name,p_password,invitation.role);
  UPDATE auth_private.workspace_invitations SET accepted_at=now() WHERE id=invitation.id;
  RETURN new_user;
END $$;
REVOKE ALL ON FUNCTION auth_private.accept_workspace_invitation(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_private.accept_workspace_invitation(text,text,text) TO wf_auth;
