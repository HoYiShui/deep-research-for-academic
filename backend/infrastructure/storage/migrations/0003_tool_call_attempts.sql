-- Durable per-physical-attempt accounting; a semantic success is cached once.
ALTER TABLE tool_calls ADD COLUMN result_size BIGINT CHECK (result_size >= 0);
ALTER TABLE tool_calls ADD COLUMN result_media_type TEXT;
ALTER TABLE tool_calls ADD CONSTRAINT mono_tool_result_ref CHECK (
    (result_size IS NULL AND result_media_type IS NULL) OR
    (result_size IS NOT NULL AND result_media_type IS NOT NULL
     AND result_object_key IS NOT NULL AND result_hash IS NOT NULL)
);

CREATE TABLE tool_budget_baselines (
    run_id UUID PRIMARY KEY REFERENCES research_runs(run_id),
    checkpoint_seq INTEGER NOT NULL CHECK (checkpoint_seq > 0),
    usage JSONB NOT NULL CHECK (jsonb_typeof(usage) = 'object'),
    elapsed_s DOUBLE PRECISION NOT NULL CHECK (elapsed_s >= 0 AND elapsed_s < 'Infinity'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE tool_call_attempts (
    call_id TEXT NOT NULL REFERENCES tool_calls(call_id),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    lease_token BIGINT NOT NULL CHECK (lease_token > 0),
    tool TEXT NOT NULL CHECK (tool IN ('llm','search','fetch','analysis')),
    status TEXT NOT NULL CHECK (status IN ('reserved','succeeded','failed','uncertain')),
    tokens_reserved INTEGER NOT NULL CHECK (tokens_reserved >= 0),
    tokens_used INTEGER CHECK (tokens_used >= 0),
    uncertain_replay BOOLEAN NOT NULL,
    failure JSONB CHECK (failure IS NULL OR jsonb_typeof(failure) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(call_id,attempt),
    CHECK ((tool='llm' AND tokens_reserved > 0) OR (tool<>'llm' AND tokens_reserved=0)),
    CHECK (status<>'reserved' OR tokens_used IS NULL)
);
CREATE INDEX mono_attempts_by_call ON tool_call_attempts(call_id,status);
