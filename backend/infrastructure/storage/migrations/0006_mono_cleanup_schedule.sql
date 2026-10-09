-- Private lifecycle scheduling only; retain all existing content/history facts.
ALTER TABLE knowledge_bases ADD COLUMN cleanup_not_before TIMESTAMPTZ;
ALTER TABLE knowledge_bases ADD COLUMN cleanup_verified_at TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN cleanup_not_before TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN cleanup_verified_at TIMESTAMPTZ;
CREATE INDEX mono_kb_tombstone_scan ON knowledge_bases(cleanup_verified_at,kb_id)
    WHERE status='deleted';
CREATE INDEX mono_document_tombstone_scan ON documents(cleanup_verified_at,document_id)
    WHERE status='deleted';
