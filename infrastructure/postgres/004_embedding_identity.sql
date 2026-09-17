-- Legacy rows cannot be attributed safely: re-ingest documents to populate this tag.
ALTER TABLE knowledge_chunks ADD COLUMN embedding_identity text NOT NULL DEFAULT 'legacy:unverified';
CREATE INDEX knowledge_representation ON knowledge_chunks(tenant_id, agent_id, embedding_identity);
