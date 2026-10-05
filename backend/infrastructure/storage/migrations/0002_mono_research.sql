-- mono-v1 research persistence. Existing facts stay intact and read-only.
-- A legacy session has no trustworthy explicit confirmation/run/seq identity;
-- do not synthesize one or attach unowned sessions to the development user.

ALTER TABLE users RENAME TO legacy_users;
ALTER TABLE sessions RENAME TO legacy_sessions;
ALTER TABLE messages RENAME TO legacy_messages;
ALTER TABLE briefs RENAME TO legacy_briefs;
ALTER TABLE reports RENAME TO legacy_reports;
ALTER TABLE phase_snapshots RENAME TO legacy_phase_snapshots;

CREATE TABLE legacy_migration_records (
    resource_type TEXT NOT NULL CHECK (resource_type IN ('user','session','message','brief','report','checkpoint')),
    legacy_id TEXT NOT NULL,
    new_id UUID,
    disposition TEXT NOT NULL CHECK (disposition IN ('mapped','isolated')),
    reason TEXT NOT NULL,
    preserved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mono_legacy_record_pk PRIMARY KEY (resource_type,legacy_id)
);

CREATE TABLE users (
    user_id UUID CONSTRAINT mono_users_pk PRIMARY KEY,
    email TEXT NOT NULL CONSTRAINT mono_users_email_unique UNIQUE,
    password_hash TEXT,
    is_development BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (email = lower(btrim(email)) AND char_length(email) BETWEEN 3 AND 254),
    CHECK ((is_development AND password_hash IS NULL) OR
           (NOT is_development AND password_hash IS NOT NULL AND char_length(btrim(password_hash)) > 0))
);

-- Map only unambiguous UUID/email users, without guessing or merging identities.
-- Password hashes remain exact; algorithm compatibility is handled by Auth.
CREATE TEMP TABLE mono_user_candidates ON COMMIT DROP AS
WITH candidates AS (
    SELECT *, lower(btrim(email)) AS normalized_email,
           CASE WHEN user_id ~* '^([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$'
                THEN user_id::uuid ELSE NULL END AS normalized_id
    FROM legacy_users
)
SELECT *, count(*) OVER (PARTITION BY normalized_email) AS email_count,
          count(*) OVER (PARTITION BY normalized_id) AS identity_count
FROM candidates;

INSERT INTO users(user_id,email,password_hash,is_development,created_at)
SELECT normalized_id,normalized_email,password_hash,false,created_at
FROM mono_user_candidates
WHERE normalized_id IS NOT NULL AND email_count=1 AND identity_count=1
      AND char_length(normalized_email) BETWEEN 3 AND 254
      AND char_length(btrim(password_hash)) > 0
      AND normalized_id <> '00000000-0000-4000-8000-000000000001'::uuid;

INSERT INTO legacy_migration_records(resource_type,legacy_id,new_id,disposition,reason)
SELECT 'user',c.user_id,u.user_id,
       CASE WHEN u.user_id IS NULL THEN 'isolated' ELSE 'mapped' END,
       CASE WHEN u.user_id IS NOT NULL THEN 'unambiguous_uuid_and_normalized_email'
            WHEN c.normalized_id IS NULL THEN 'invalid_uuid'
            WHEN c.identity_count<>1 THEN 'normalized_uuid_collision'
            WHEN c.email_count<>1 THEN 'normalized_email_collision'
            WHEN c.normalized_id='00000000-0000-4000-8000-000000000001'::uuid THEN 'reserved_development_identity'
            ELSE 'invalid_user_fields' END
FROM mono_user_candidates c LEFT JOIN users u ON u.user_id=c.normalized_id;

INSERT INTO legacy_migration_records(resource_type,legacy_id,disposition,reason)
SELECT 'session',session_id,'isolated','legacy_confirmation_ownership_and_checkpoint_not_reconstructible' FROM legacy_sessions
UNION ALL SELECT 'message',message_id::text,'isolated','belongs_to_preserved_legacy_session' FROM legacy_messages
UNION ALL SELECT 'brief',brief_id::text,'isolated','no_validated_explicit_freeze_identity' FROM legacy_briefs
UNION ALL SELECT 'report',report_id::text,'isolated','legacy_report_preserved_without_invented_run' FROM legacy_reports
UNION ALL SELECT 'checkpoint',snapshot_id::text,'isolated','no_monotonic_run_sequence' FROM legacy_phase_snapshots;

CREATE FUNCTION mono_reject_legacy_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Preserved legacy data is read-only' USING ERRCODE='55000';
END;
$$;
DO $$
DECLARE name TEXT;
BEGIN
    FOREACH name IN ARRAY ARRAY['legacy_users','legacy_sessions','legacy_messages','legacy_briefs','legacy_reports','legacy_phase_snapshots']
    LOOP
        EXECUTE format('CREATE TRIGGER preserve_legacy BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION mono_reject_legacy_mutation()', name);
    END LOOP;
END;
$$;

CREATE FUNCTION mono_valid_brief(body JSONB, require_complete BOOLEAN) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE item RECORD; value TEXT; key_count INTEGER;
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    SELECT count(*) INTO key_count FROM jsonb_object_keys(body);
    IF require_complete AND key_count<>10 THEN RETURN false; END IF;
    FOR item IN SELECT * FROM jsonb_each(body) LOOP
        IF item.key NOT IN ('task_type','decision_goal','research_object','scope','comparison_scope',
                            'claims_to_verify','evidence_requirements','conclusion_boundary','deliverable','assumptions')
           OR jsonb_typeof(item.value) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
        value := body->>item.key;
        IF item.key='task_type' THEN
            IF value NOT IN ('idea_exploration','method_differentiation','evaluation_design') THEN RETURN false; END IF;
        ELSIF char_length(value)>8000 OR (item.key<>'assumptions' AND char_length(btrim(value))=0) THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END;
$$;

CREATE TABLE sessions (
    session_id UUID CONSTRAINT mono_sessions_pk PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES users(user_id),
    query TEXT NOT NULL CHECK (char_length(btrim(query)) BETWEEN 1 AND 16000),
    status TEXT NOT NULL CHECK (status IN ('ask','confirm','ready','running','cancelling','completed','failed','cancelled')),
    revision BIGINT NOT NULL CHECK (revision>=1),
    brief_draft JSONB NOT NULL CHECK (mono_valid_brief(brief_draft,false)),
    brief_version INTEGER NOT NULL CHECK (brief_version>=1),
    pending_questions JSONB NOT NULL CHECK (jsonb_typeof(pending_questions)='array'),
    missing_fields JSONB NOT NULL CHECK (jsonb_typeof(missing_fields)='array'),
    clarification_round INTEGER NOT NULL CHECK (clarification_round>=0),
    clarification_limit_reached BOOLEAN NOT NULL,
    source_selection JSONB NOT NULL CHECK (jsonb_typeof(source_selection)='object'),
    run_id UUID,
    failure JSONB CHECK (failure IS NULL OR jsonb_typeof(failure)='object'),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT mono_session_owner_pair UNIQUE(session_id,owner_id)
);
CREATE INDEX mono_sessions_owner ON sessions(owner_id,created_at,session_id);

CREATE TABLE briefs (
    session_id UUID NOT NULL REFERENCES sessions(session_id),
    version INTEGER NOT NULL CHECK (version>=1),
    content JSONB NOT NULL,
    frozen_at TIMESTAMPTZ,
    confirmed_by UUID REFERENCES users(user_id),
    content_hash TEXT CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    source_selection JSONB NOT NULL CHECK (jsonb_typeof(source_selection)='object'),
    CONSTRAINT mono_briefs_pk PRIMARY KEY (session_id,version),
    CONSTRAINT mono_frozen_brief_identity UNIQUE(session_id,version,content_hash),
    FOREIGN KEY(session_id,confirmed_by) REFERENCES sessions(session_id,owner_id),
    CHECK (mono_valid_brief(content,frozen_at IS NOT NULL)),
    CHECK ((frozen_at IS NULL AND confirmed_by IS NULL AND content_hash IS NULL) OR
           (frozen_at IS NOT NULL AND confirmed_by IS NOT NULL AND content_hash IS NOT NULL))
);
ALTER TABLE sessions ADD CONSTRAINT mono_session_current_brief
    FOREIGN KEY(session_id,brief_version) REFERENCES briefs(session_id,version) DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION mono_guard_frozen_brief() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.frozen_at IS NOT NULL AND (TG_OP='DELETE' OR NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Frozen brief is immutable' USING ERRCODE='55000';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER mono_brief_immutable BEFORE UPDATE OR DELETE ON briefs
    FOR EACH ROW EXECUTE FUNCTION mono_guard_frozen_brief();

CREATE TABLE messages (
    message_id UUID CONSTRAINT mono_messages_pk PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(session_id),
    sequence BIGINT NOT NULL CHECK (sequence>=1),
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    kind TEXT NOT NULL CHECK (kind IN ('initial','answer','assessment','confirmation','rejection')),
    content TEXT NOT NULL CHECK (char_length(btrim(content))>0),
    assessment JSONB CHECK (assessment IS NULL OR jsonb_typeof(assessment)='object'),
    brief_version INTEGER NOT NULL CHECK (brief_version>=1),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT mono_message_sequence UNIQUE(session_id,sequence),
    FOREIGN KEY(session_id,brief_version) REFERENCES briefs(session_id,version) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE research_runs (
    run_id UUID CONSTRAINT mono_runs_pk PRIMARY KEY,
    session_id UUID NOT NULL CONSTRAINT mono_one_run_per_session UNIQUE REFERENCES sessions(session_id),
    brief_version INTEGER NOT NULL CHECK (brief_version>=1),
    brief_hash TEXT NOT NULL CHECK (brief_hash ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('ready','running','cancelling','completed','failed','cancelled')),
    phase TEXT NOT NULL CHECK (phase IN ('plan','research','analyze','write','review','done')),
    attempt_count INTEGER NOT NULL CHECK (attempt_count>=0),
    checkpoint_seq BIGINT NOT NULL CHECK (checkpoint_seq>=1),
    cancel_requested_at TIMESTAMPTZ,
    lease_owner TEXT CHECK (lease_owner IS NULL OR char_length(btrim(lease_owner))>0),
    lease_token BIGINT NOT NULL CHECK (lease_token>=0),
    lease_expires_at TIMESTAMPTZ,
    resume_allowed BOOLEAN NOT NULL,
    failure JSONB CHECK (failure IS NULL OR jsonb_typeof(failure)='object'),
    config_snapshot JSONB NOT NULL CHECK (jsonb_typeof(config_snapshot)='object'),
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CONSTRAINT mono_run_session_pair UNIQUE(run_id,session_id),
    FOREIGN KEY(session_id,brief_version,brief_hash) REFERENCES briefs(session_id,version,content_hash),
    CHECK ((status='completed')=(phase='done')),
    CHECK ((lease_owner IS NULL)=(lease_expires_at IS NULL)),
    CHECK (status<>'running' OR (lease_owner IS NOT NULL AND attempt_count>0 AND lease_token>0)),
    CHECK ((status IN ('completed','failed','cancelled'))=(finished_at IS NOT NULL))
);
ALTER TABLE sessions ADD CONSTRAINT mono_session_run_pair
    FOREIGN KEY(run_id,session_id) REFERENCES research_runs(run_id,session_id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX mono_runnable_runs ON research_runs(status,created_at,run_id);
CREATE INDEX mono_expired_run_leases ON research_runs(lease_expires_at) WHERE lease_owner IS NOT NULL;

CREATE TABLE phase_snapshots (
    snapshot_id UUID CONSTRAINT mono_snapshots_pk PRIMARY KEY,
    run_id UUID NOT NULL,
    session_id UUID NOT NULL,
    seq BIGINT NOT NULL CHECK (seq>=1),
    schema_version INTEGER NOT NULL CHECK (schema_version=1),
    phase TEXT NOT NULL CHECK (phase IN ('plan','research','analyze','write','review','done')),
    state JSONB NOT NULL CHECK (jsonb_typeof(state)='object'),
    state_hash TEXT NOT NULL CHECK (state_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT mono_checkpoint_sequence UNIQUE(run_id,seq),
    FOREIGN KEY(run_id,session_id) REFERENCES research_runs(run_id,session_id),
    CHECK (state ?& ARRAY['schema_version','session_id','run_id','brief_version','brief_hash','phase',
                         'research_brief','source_selection','section_plans','sources','evidence','claims',
                         'claim_evidence_links','quantitative_observations','comparable_metrics','comparison_sets',
                         'analysis_artifacts','section_coverage','draft_sections','draft_claim_bindings',
                         'critic_feedback','draft_version','reviewed_draft_version','review_verdict','final_report',
                         'run_metadata','errors']),
    CHECK (state->>'schema_version'='1' AND state->>'run_id'=run_id::text
           AND state->>'session_id'=session_id::text AND state->>'phase'=phase)
);
ALTER TABLE research_runs ADD CONSTRAINT mono_run_latest_checkpoint
    FOREIGN KEY(run_id,checkpoint_seq) REFERENCES phase_snapshots(run_id,seq) DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION mono_guard_checkpoint_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE frozen research_runs%ROWTYPE;
BEGIN
    SELECT * INTO frozen FROM research_runs WHERE run_id=NEW.run_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Checkpoint run missing' USING ERRCODE='23503'; END IF;
    IF NEW.state->>'brief_hash' IS DISTINCT FROM frozen.brief_hash
       OR NEW.state->>'brief_version' IS DISTINCT FROM frozen.brief_version::text
       OR NEW.state->'run_metadata'->'config' IS DISTINCT FROM frozen.config_snapshot THEN
        RAISE EXCEPTION 'Checkpoint changed frozen input or config' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER mono_checkpoint_input BEFORE INSERT ON phase_snapshots
    FOR EACH ROW EXECUTE FUNCTION mono_guard_checkpoint_input();

CREATE TABLE reports (
    report_id UUID CONSTRAINT mono_reports_pk PRIMARY KEY,
    run_id UUID NOT NULL CONSTRAINT mono_one_report_per_run UNIQUE,
    session_id UUID NOT NULL,
    version INTEGER NOT NULL CHECK (version>=1),
    content JSONB NOT NULL CHECK (jsonb_typeof(content)='object'),
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY(run_id,session_id) REFERENCES research_runs(run_id,session_id),
    CONSTRAINT mono_report_version UNIQUE(session_id,version)
);

CREATE FUNCTION mono_reject_fact_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Committed fact is immutable' USING ERRCODE='55000';
END;
$$;
CREATE TRIGGER mono_messages_append_only BEFORE UPDATE OR DELETE ON messages
    FOR EACH ROW EXECUTE FUNCTION mono_reject_fact_mutation();
CREATE TRIGGER mono_checkpoint_immutable BEFORE UPDATE OR DELETE ON phase_snapshots
    FOR EACH ROW EXECUTE FUNCTION mono_reject_fact_mutation();
CREATE TRIGGER mono_report_immutable BEFORE UPDATE OR DELETE ON reports
    FOR EACH ROW EXECUTE FUNCTION mono_reject_fact_mutation();

CREATE TABLE tool_calls (
    call_id TEXT CONSTRAINT mono_tool_calls_pk PRIMARY KEY CHECK (char_length(btrim(call_id))>0),
    run_id UUID NOT NULL REFERENCES research_runs(run_id),
    call_key TEXT NOT NULL CHECK (char_length(btrim(call_key))>0),
    status TEXT NOT NULL CHECK (status IN ('reserved','succeeded','failed','uncertain')),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    result_object_key TEXT,
    result_hash TEXT CHECK (result_hash ~ '^[a-f0-9]{64}$'),
    failure JSONB CHECK (failure IS NULL OR jsonb_typeof(failure)='object'),
    budget_units INTEGER NOT NULL CHECK (budget_units>=0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT mono_tool_call_key UNIQUE(run_id,call_key),
    CHECK (status<>'succeeded' OR (result_object_key IS NOT NULL AND result_hash IS NOT NULL))
);

CREATE TABLE idempotency_requests (
    owner_id UUID NOT NULL REFERENCES users(user_id),
    operation TEXT NOT NULL CHECK (char_length(btrim(operation))>0),
    key TEXT NOT NULL CHECK (char_length(btrim(key)) BETWEEN 1 AND 128),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('in_progress','completed')),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    response_status INTEGER CHECK (response_status BETWEEN 100 AND 599),
    response_body JSONB,
    resource_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mono_idempotency_pk PRIMARY KEY(owner_id,operation,key),
    CHECK ((state='in_progress' AND response_status IS NULL AND response_body IS NULL)
           OR (state='completed' AND response_status IS NOT NULL))
);
CREATE INDEX mono_request_retention ON idempotency_requests(updated_at) WHERE state='completed';
