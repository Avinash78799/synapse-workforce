-- Applied by the migration owner, never by a runtime connection.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS auth_private;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO wf_app, wf_dispatch;
GRANT USAGE ON SCHEMA auth_private TO wf_auth;

CREATE TABLE tenants (
  id uuid PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE auth_private.users (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
  email text NOT NULL UNIQUE, name text NOT NULL, password_hash text NOT NULL,
  role text NOT NULL CHECK(role IN ('owner','operator','finance_approver','viewer')),
  active boolean NOT NULL DEFAULT true
);
CREATE TABLE auth_private.sessions (
  token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth_private.users(id), tenant_id uuid NOT NULL REFERENCES tenants(id),
  csrf_token text NOT NULL, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON auth_private.users TO wf_auth;
GRANT SELECT, INSERT, DELETE ON auth_private.sessions TO wf_auth;

CREATE TABLE agents (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
  kind text NOT NULL CHECK(kind IN ('operations','support')), name text NOT NULL,
  owner_name text NOT NULL, version text NOT NULL DEFAULT '1.0.0',
  risk_level text NOT NULL DEFAULT 'medium', status text NOT NULL DEFAULT 'active'
    CHECK(status IN ('active','draining','hard_aborted','inactive')),
  autonomy integer NOT NULL DEFAULT 4 CHECK(autonomy BETWEEN 0 AND 5),
  kill_reason text, policy_version integer NOT NULL DEFAULT 1, execution_epoch integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id, id), UNIQUE(tenant_id, kind)
);
CREATE TABLE agent_versions (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, version text NOT NULL,
  definition jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,agent_id,version), FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id)
);
CREATE TABLE agent_skills (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, name text NOT NULL, enabled boolean NOT NULL DEFAULT true,
  PRIMARY KEY(tenant_id,agent_id,name), FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id)
);
CREATE TABLE agent_tools (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, name text NOT NULL, enabled boolean NOT NULL DEFAULT true,
  PRIMARY KEY(tenant_id,agent_id,name), FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id)
);
CREATE TABLE agent_permissions (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, action text NOT NULL, allowed boolean NOT NULL,
  PRIMARY KEY(tenant_id,agent_id,action), FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id)
);
CREATE TABLE agent_policies (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, version integer NOT NULL,
  rules jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), created_by text NOT NULL,
  PRIMARY KEY(tenant_id,agent_id,version), FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id)
);
CREATE TABLE agent_runs (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL,
  actor_id uuid NOT NULL, objective text NOT NULL, request_key uuid NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK(status IN
    ('queued','running','waiting_approval','completed','escalated','failed','paused','aborted')),
  agent_version text NOT NULL, policy_version integer NOT NULL, workflow_version text NOT NULL DEFAULT '1.0',
  provider text NOT NULL, model text, input jsonb NOT NULL DEFAULT '{}', plan jsonb,
  summary jsonb NOT NULL DEFAULT '{}', error text,
  input_tokens integer NOT NULL DEFAULT 0, output_tokens integer NOT NULL DEFAULT 0,
  cost_usd numeric(12,6), model_attempts integer NOT NULL DEFAULT 0, execution_epoch integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
  UNIQUE(tenant_id,agent_id,request_key), UNIQUE(tenant_id,agent_id,id),
  FOREIGN KEY(tenant_id,agent_id) REFERENCES agents(tenant_id,id)
);
CREATE TABLE agent_tasks (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL, run_id uuid NOT NULL,
  position integer NOT NULL, tool text NOT NULL, arguments jsonb NOT NULL,
  status text NOT NULL DEFAULT 'pending', decision text, reason text,
  policy_version integer NOT NULL, approval_id uuid, idempotency_key text NOT NULL,
  attempts integer NOT NULL DEFAULT 0, verification text, result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  UNIQUE(tenant_id,agent_id,run_id,position), UNIQUE(tenant_id,agent_id,id),
  FOREIGN KEY(tenant_id,agent_id,run_id) REFERENCES agent_runs(tenant_id,agent_id,id)
);
CREATE TABLE approval_requests (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL, run_id uuid NOT NULL,
  task_id uuid NOT NULL, policy_version integer NOT NULL, action_digest text NOT NULL,
  required_role text NOT NULL, reason text NOT NULL, expires_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','denied','expired')),
  decided_by uuid, decision_reason text, decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,agent_id,task_id), FOREIGN KEY(tenant_id,agent_id,task_id) REFERENCES agent_tasks(tenant_id,agent_id,id)
);
CREATE TABLE tool_receipts (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, idempotency_key text NOT NULL,
  task_id uuid NOT NULL, tool text NOT NULL, arguments_digest text NOT NULL,
  state text NOT NULL CHECK(state IN ('dispatched','confirmed','unknown','failed')),
  result jsonb, dispatch_count integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,agent_id,idempotency_key)
);
CREATE TABLE business_cases (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, case_key text NOT NULL,
  run_id uuid NOT NULL, status text NOT NULL DEFAULT 'open',
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,agent_id,case_key)
);
CREATE TABLE audit_logs (
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid,
  run_id uuid, task_id uuid, actor text NOT NULL, agent_version text NOT NULL,
  action text NOT NULL, tool text, policy_version text NOT NULL, approval_id uuid,
  result text NOT NULL, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE agent_events (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL, run_id uuid,
  type text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE memory_items (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL, run_id uuid,
  kind text NOT NULL CHECK(kind IN ('working','episodic','semantic','procedural')),
  content jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_documents (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL, title text NOT NULL,
  content text NOT NULL, trust text NOT NULL DEFAULT 'untrusted', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_chunks (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL,
  document_id uuid NOT NULL REFERENCES knowledge_documents(id), content text NOT NULL,
  embedding vector(64) NOT NULL
);
CREATE TABLE integrations (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL, name text NOT NULL,
  status text NOT NULL, scopes jsonb NOT NULL DEFAULT '[]', UNIQUE(tenant_id,agent_id,name)
);
CREATE TABLE credentials (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL,
  integration_id uuid NOT NULL REFERENCES integrations(id), secret_ref uuid NOT NULL
);
CREATE TABLE workflow_definitions (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, name text NOT NULL, version text NOT NULL,
  definition jsonb NOT NULL, PRIMARY KEY(tenant_id,agent_id,name,version)
);
CREATE TABLE evaluation_scenarios (
  id text NOT NULL, tenant_id uuid NOT NULL, agent_id uuid NOT NULL, name text NOT NULL,
  input jsonb NOT NULL, expected jsonb NOT NULL, PRIMARY KEY(tenant_id,agent_id,id)
);
CREATE TABLE evaluation_runs (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL,
  suite_version text NOT NULL, passed integer NOT NULL, total integer NOT NULL,
  results jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
-- The simulation is a replaceable tool backend. Every copy belongs to exactly one agent.
CREATE TABLE sim_records (
  tenant_id uuid NOT NULL, agent_id uuid NOT NULL, kind text NOT NULL
    CHECK(kind IN ('customer','order','payment','product','inventory','shipment','ticket','employee','task','message','refund')),
  id text NOT NULL, data jsonb NOT NULL, PRIMARY KEY(tenant_id,agent_id,kind,id)
);
CREATE TABLE queue_outbox (
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL, agent_id uuid NOT NULL, run_id uuid NOT NULL,
  published_at timestamptz, available_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX runs_recent ON agent_runs(tenant_id,agent_id,created_at DESC);
CREATE INDEX tasks_run ON agent_tasks(tenant_id,agent_id,run_id,position);
CREATE INDEX audit_recent ON audit_logs(tenant_id,created_at DESC);
CREATE INDEX approvals_pending ON approval_requests(tenant_id,status,expires_at);
CREATE INDEX memory_scope ON memory_items(tenant_id,agent_id,created_at DESC);
CREATE INDEX knowledge_scope ON knowledge_chunks(tenant_id,agent_id);
CREATE INDEX outbox_unpublished ON queue_outbox(id) WHERE published_at IS NULL;

-- Agents see only their own data. A server-authenticated operator uses the
-- explicit 'control' scope to manage agents inside their own tenant.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['agent_versions','agent_skills','agent_tools','agent_permissions',
    'agent_policies','agent_runs','agent_tasks','approval_requests','tool_receipts','business_cases',
    'audit_logs','agent_events','memory_items','knowledge_documents','knowledge_chunks','integrations',
    'credentials','workflow_definitions','evaluation_scenarios','evaluation_runs','sim_records','queue_outbox']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY runtime_access ON %I TO wf_app USING(true) WITH CHECK(true)', t);
    EXECUTE format('CREATE POLICY scope ON %I AS RESTRICTIVE TO wf_app USING
      (tenant_id::text = current_setting(''app.tenant_id'', true) AND
       (current_setting(''app.agent_id'', true) = ''control'' OR agent_id::text = current_setting(''app.agent_id'', true)))
      WITH CHECK (tenant_id::text = current_setting(''app.tenant_id'', true) AND
       (current_setting(''app.agent_id'', true) = ''control'' OR agent_id::text = current_setting(''app.agent_id'', true)))', t);
  END LOOP;
END $$;
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_access ON tenants TO wf_app USING(true);
CREATE POLICY tenant_scope ON tenants AS RESTRICTIVE TO wf_app USING(id::text=current_setting('app.tenant_id',true));
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_access ON agents TO wf_app USING(true) WITH CHECK(true);
CREATE POLICY agent_scope ON agents AS RESTRICTIVE TO wf_app USING(tenant_id::text=current_setting('app.tenant_id',true)
  AND (current_setting('app.agent_id',true)='control' OR id::text=current_setting('app.agent_id',true)));
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO wf_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO wf_app;
-- Immutable audit and version records: no app-level edits, even for an owner.
REVOKE UPDATE, DELETE ON audit_logs, agent_versions, agent_policies, credentials FROM wf_app;
REVOKE INSERT, UPDATE, DELETE ON tenants FROM wf_app;
REVOKE ALL ON schema_migrations FROM wf_app;
CREATE POLICY dispatch_scope ON queue_outbox TO wf_dispatch USING(true) WITH CHECK(true);
GRANT SELECT, UPDATE ON queue_outbox TO wf_dispatch;
