-- Minimal V1 schema for the state store (durable truth + phase snapshots).
-- Matches the queries in infrastructure/storage/postgres.py: sessions stores
-- the SessionState JSONB; phase_snapshots stores PipelineState JSONB per phase
-- with created_at ordering so recovery takes the latest row for a phase.

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    state JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS phase_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    state JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_phase_snapshots_session_phase
    ON phase_snapshots (session_id, phase, created_at DESC);
