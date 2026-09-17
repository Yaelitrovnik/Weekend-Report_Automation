-- PostgreSQL target schema. The local repository adapter applies an equivalent
-- sqlite schema for tests and offline development.
CREATE TABLE IF NOT EXISTS runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    automation_status TEXT,
    started_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    worker_id TEXT,
    last_heartbeat TIMESTAMPTZ,
    current_module TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    review_decision TEXT,
    application_version TEXT,
    build_id TEXT,
    git_commit TEXT,
    config_version TEXT,
    final_snapshot_path TEXT,
    final_pdf_path TEXT,
    final_pdf_checksum TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    module TEXT NOT NULL,
    site TEXT,
    check_id TEXT NOT NULL,
    target TEXT,
    status TEXT NOT NULL,
    expected_json JSONB,
    actual_json JSONB,
    message TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS evidence (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    result_id BIGINT REFERENCES results(id),
    module TEXT NOT NULL,
    site TEXT,
    evidence_type TEXT NOT NULL,
    path TEXT NOT NULL,
    checksum TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS review_notes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    scope TEXT NOT NULL,
    module TEXT,
    result_id BIGINT,
    dashboard_id TEXT,
    author TEXT NOT NULL,
    note TEXT NOT NULL,
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS run_lock (
    name TEXT PRIMARY KEY,
    active_run_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);
