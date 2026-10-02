-- 0001_init: baseline schema (V1, 6 tables; audit_log deferred to V2).
--
-- Stable concepts (queried by field) get real tables; the evolving pipeline
-- state (claims/evidence/...) stays in phase_snapshots.state JSONB.

CREATE TABLE users (
    user_id       TEXT PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT REFERENCES users(user_id),
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    message_id  BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE briefs (
    brief_id    BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    task_type   TEXT,
    brief       JSONB NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    frozen_at   TIMESTAMPTZ,
    UNIQUE (session_id, version)
);

CREATE TABLE reports (
    report_id   BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    version     INTEGER NOT NULL DEFAULT 1,
    content     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, version)
);

CREATE TABLE phase_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    phase       TEXT NOT NULL,
    state       JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_session       ON messages (session_id, created_at);
CREATE INDEX idx_briefs_session_version ON briefs (session_id, version);
CREATE INDEX idx_reports_session_version ON reports (session_id, version);
CREATE INDEX idx_snapshots_session_phase ON phase_snapshots (session_id, phase, created_at DESC);
