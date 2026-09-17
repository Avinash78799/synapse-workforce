-- Additive upgrade: preserve existing policy versions and historical audit rows.
ALTER TABLE approval_requests ADD COLUMN independent_required boolean NOT NULL DEFAULT false;
UPDATE approval_requests a SET independent_required=true
FROM agent_tasks t WHERE t.id=a.task_id AND t.tool='payment.refund'
  AND (t.arguments->>'amount_minor')::bigint >= 500000;
ALTER TABLE agent_skills ADD COLUMN autonomy_cap integer CHECK(autonomy_cap BETWEEN 0 AND 5);
ALTER TABLE audit_logs ADD COLUMN chain_seq bigint;
ALTER TABLE audit_logs ADD COLUMN previous_hash text;
ALTER TABLE audit_logs ADD COLUMN event_hash text;
ALTER TABLE audit_logs ADD COLUMN signature text;
ALTER TABLE audit_logs ADD COLUMN key_id text;
CREATE UNIQUE INDEX audit_chain_position ON audit_logs
  (tenant_id, COALESCE(agent_id,'00000000-0000-0000-0000-000000000000'::uuid),chain_seq)
  WHERE chain_seq IS NOT NULL;
CREATE INDEX unresolved_receipts ON tool_receipts(tenant_id,agent_id,updated_at) WHERE state='unknown';
