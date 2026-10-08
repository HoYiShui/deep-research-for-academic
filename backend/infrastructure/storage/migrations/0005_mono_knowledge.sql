-- Additive mono knowledge facts. No legacy/user data is dropped or synthesized.
CREATE TABLE knowledge_bases (
    kb_id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES users(user_id),
    name TEXT NOT NULL CHECK (name = btrim(name) AND char_length(name) BETWEEN 1 AND 100),
    description TEXT CHECK (char_length(description) <= 2000),
    data_classification TEXT NOT NULL DEFAULT 'private' CHECK (data_classification IN ('public','private')),
    status TEXT NOT NULL CHECK (status IN ('creating','active','deleting','deleted')),
    revision BIGINT NOT NULL CHECK (revision > 0),
    index_version TEXT NOT NULL CHECK (char_length(btrim(index_version)) > 0),
    cleanup_cursor TEXT,
    failure JSONB,
    lease_owner TEXT,
    lease_token BIGINT NOT NULL DEFAULT 0 CHECK (lease_token >= 0),
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (lease_owner IS NULL OR lease_token > 0)
);
CREATE UNIQUE INDEX mono_kb_owner_name ON knowledge_bases(owner_id,name) WHERE status <> 'deleted';
CREATE INDEX mono_kb_owner ON knowledge_bases(owner_id,created_at,kb_id);

CREATE TABLE documents (
    document_id UUID PRIMARY KEY,
    kb_id UUID NOT NULL REFERENCES knowledge_bases(kb_id),
    filename TEXT NOT NULL CHECK (char_length(btrim(filename)) > 0),
    media_type TEXT NOT NULL CHECK (media_type = 'application/pdf'),
    status TEXT NOT NULL CHECK (status IN ('active','deleting','deleted')),
    active_version_id UUID,
    revision BIGINT NOT NULL CHECK (revision > 0),
    cleanup_cursor TEXT,
    failure JSONB,
    lease_owner TEXT,
    lease_token BIGINT NOT NULL DEFAULT 0 CHECK (lease_token >= 0),
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(document_id,kb_id),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (lease_owner IS NULL OR lease_token > 0)
);
CREATE INDEX mono_document_kb ON documents(kb_id,created_at,document_id);

CREATE TABLE document_versions (
    document_version_id UUID PRIMARY KEY,
    document_id UUID NOT NULL,
    kb_id UUID NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    ingestion_version TEXT NOT NULL CHECK (char_length(btrim(ingestion_version)) > 0),
    index_version TEXT NOT NULL CHECK (char_length(btrim(index_version)) > 0),
    source_object_key TEXT NOT NULL CHECK (char_length(btrim(source_object_key)) > 0),
    parsed_object_key TEXT,
    manifest_object_key TEXT,
    chunk_count INTEGER NOT NULL CHECK (chunk_count BETWEEN 0 AND 10000),
    status TEXT NOT NULL CHECK (status IN ('staging','active','failed','retired')),
    created_at TIMESTAMPTZ NOT NULL,
    activated_at TIMESTAMPTZ,
    CONSTRAINT mono_version_parent FOREIGN KEY(document_id,kb_id) REFERENCES documents(document_id,kb_id),
    CONSTRAINT mono_version_content UNIQUE(kb_id,content_hash,ingestion_version),
    UNIQUE(document_version_id,document_id,kb_id),
    CHECK (status NOT IN ('active','retired') OR (activated_at IS NOT NULL AND chunk_count > 0))
);
CREATE UNIQUE INDEX mono_version_active ON document_versions(document_id) WHERE status='active';
ALTER TABLE documents ADD CONSTRAINT mono_document_active_parent
    FOREIGN KEY(active_version_id,document_id,kb_id)
    REFERENCES document_versions(document_version_id,document_id,kb_id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE ingestion_jobs (
    job_id UUID PRIMARY KEY,
    document_version_id UUID NOT NULL UNIQUE,
    document_id UUID NOT NULL,
    kb_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (char_length(btrim(idempotency_key)) BETWEEN 1 AND 128),
    status TEXT NOT NULL CHECK (status IN ('accepted','processing','cancelling','completed','failed','cancelled')),
    attempt_count INTEGER NOT NULL CHECK (attempt_count BETWEEN 0 AND 3),
    attempt_history JSONB NOT NULL CHECK (jsonb_typeof(attempt_history)='array'),
    progress JSONB NOT NULL CHECK (jsonb_typeof(progress)='object'),
    cancel_requested_at TIMESTAMPTZ,
    lease_owner TEXT,
    lease_token BIGINT NOT NULL DEFAULT 0 CHECK (lease_token >= 0),
    lease_expires_at TIMESTAMPTZ,
    failure JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CONSTRAINT mono_job_parent FOREIGN KEY(document_version_id,document_id,kb_id)
        REFERENCES document_versions(document_version_id,document_id,kb_id),
    CHECK (jsonb_array_length(attempt_history)=attempt_count),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (lease_owner IS NULL OR lease_token > 0),
    CHECK (status <> 'processing' OR (lease_owner IS NOT NULL AND attempt_count > 0)),
    CHECK (status NOT IN ('completed','failed','cancelled') OR (finished_at IS NOT NULL AND lease_owner IS NULL))
);
CREATE UNIQUE INDEX mono_document_active_job ON ingestion_jobs(document_id)
    WHERE status IN ('accepted','processing','cancelling');
CREATE INDEX mono_jobs_scan ON ingestion_jobs(status,lease_expires_at,created_at);

CREATE TABLE chunks (
    chunk_id TEXT PRIMARY KEY CHECK (char_length(btrim(chunk_id)) > 0),
    kb_id UUID NOT NULL,
    document_id UUID NOT NULL,
    document_version_id UUID NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 9999),
    chunk_type TEXT NOT NULL CHECK (chunk_type IN ('text','table','formula')),
    content_object_key TEXT NOT NULL CHECK (char_length(btrim(content_object_key)) > 0),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    embedding_anchor TEXT NOT NULL CHECK (char_length(btrim(embedding_anchor)) > 0),
    location JSONB NOT NULL CHECK (jsonb_typeof(location)='object'),
    metadata JSONB NOT NULL CHECK (jsonb_typeof(metadata)='object'),
    CONSTRAINT mono_chunk_parent FOREIGN KEY(document_version_id,document_id,kb_id)
        REFERENCES document_versions(document_version_id,document_id,kb_id),
    CONSTRAINT mono_chunk_ordinal UNIQUE(document_version_id,ordinal)
);
CREATE INDEX mono_chunk_scope ON chunks(kb_id,document_version_id);

-- A pointer and the active version are one fact at commit, not two aliases.
CREATE FUNCTION mono_check_active_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE doc UUID; pointer UUID; actual UUID;
BEGIN
    doc := NEW.document_id;
    SELECT active_version_id INTO pointer FROM documents WHERE document_id=doc;
    SELECT document_version_id INTO actual FROM document_versions WHERE document_id=doc AND status='active';
    IF pointer IS DISTINCT FROM actual THEN
        RAISE EXCEPTION 'Document active version is inconsistent' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER mono_document_version_consistency
    AFTER INSERT OR UPDATE ON documents DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION mono_check_active_version();
CREATE CONSTRAINT TRIGGER mono_version_document_consistency
    AFTER INSERT OR UPDATE ON document_versions DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION mono_check_active_version();
