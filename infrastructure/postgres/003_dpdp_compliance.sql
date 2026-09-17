-- Migration 003: DPDP Act (2023) Compliance Schema
-- Covers Data Principal Consent, Rights (DSAR, Erasure, Correction), Grievance Redressal, and Breach Reporting.

CREATE TABLE IF NOT EXISTS data_consents (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  data_principal_id text NOT NULL,
  purpose text NOT NULL,
  status text NOT NULL CHECK(status IN ('granted', 'withdrawn', 'expired')),
  notice_version text NOT NULL DEFAULT '1.0',
  granted_at timestamptz NOT NULL DEFAULT now(),
  withdrawn_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}',
  UNIQUE(tenant_id, data_principal_id, purpose)
);

CREATE TABLE IF NOT EXISTS privacy_requests (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  data_principal_id text NOT NULL,
  request_type text NOT NULL CHECK(request_type IN ('access_summary', 'erasure', 'correction', 'nomination')),
  status text NOT NULL CHECK(status IN ('received', 'processing', 'completed', 'rejected')),
  sla_due_at timestamptz NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  result_summary text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS privacy_grievances (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  data_principal_id text NOT NULL,
  subject text NOT NULL,
  description text NOT NULL,
  status text NOT NULL CHECK(status IN ('open', 'under_review', 'resolved', 'escalated_to_dpbi')),
  dpo_notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);

CREATE TABLE IF NOT EXISTS data_breaches (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  incident_title text NOT NULL,
  description text NOT NULL,
  severity text NOT NULL CHECK(severity IN ('low', 'medium', 'high', 'critical')),
  affected_principals_count integer NOT NULL DEFAULT 0,
  categories_of_data text[] NOT NULL DEFAULT '{}',
  dpbi_notified boolean NOT NULL DEFAULT false,
  dpbi_notified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Indices for rapid lookup & SLA monitoring
CREATE INDEX IF NOT EXISTS consent_lookup ON data_consents(tenant_id, data_principal_id, purpose, status);
CREATE INDEX IF NOT EXISTS privacy_requests_pending ON privacy_requests(tenant_id, status, sla_due_at);
CREATE INDEX IF NOT EXISTS privacy_grievances_open ON privacy_grievances(tenant_id, status);

-- Row Level Security (RLS) enforcement per tenant
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['data_consents', 'privacy_requests', 'privacy_grievances', 'data_breaches']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY runtime_access ON %I TO wf_app USING(true) WITH CHECK(true)', t);
    EXECUTE format('CREATE POLICY scope ON %I AS RESTRICTIVE TO wf_app USING
      (tenant_id::text = current_setting(''app.tenant_id'', true))
      WITH CHECK (tenant_id::text = current_setting(''app.tenant_id'', true))', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON data_consents, privacy_requests, privacy_grievances, data_breaches TO wf_app;
