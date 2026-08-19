from __future__ import annotations

SQLITE_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL,
        automation_status TEXT,
        started_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        worker_id TEXT,
        last_heartbeat TEXT,
        current_module TEXT,
        reviewed_by TEXT,
        reviewed_at TEXT,
        review_decision TEXT,
        application_version TEXT,
        build_id TEXT,
        git_commit TEXT,
        config_version TEXT,
        final_snapshot_path TEXT,
        final_pdf_path TEXT,
        final_pdf_checksum TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        module TEXT NOT NULL,
        site TEXT,
        check_id TEXT NOT NULL,
        target TEXT,
        status TEXT NOT NULL,
        expected_json TEXT,
        actual_json TEXT,
        message TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        metadata_json TEXT,
        FOREIGN KEY (run_id) REFERENCES runs(run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        result_id INTEGER,
        module TEXT NOT NULL,
        site TEXT,
        evidence_type TEXT NOT NULL,
        path TEXT NOT NULL,
        checksum TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs(run_id),
        FOREIGN KEY (result_id) REFERENCES results(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        module TEXT,
        result_id INTEGER,
        dashboard_id TEXT,
        author TEXT NOT NULL,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
            -- The repository performs deterministic upsert lookup for note scopes.
            -- SQLite does not allow expression-based table UNIQUE constraints here.
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_lock (
        name TEXT PRIMARY KEY,
        active_run_id TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state)",
    "CREATE INDEX IF NOT EXISTS idx_results_run_module_site ON results(run_id, module, site)",
    "CREATE INDEX IF NOT EXISTS idx_notes_run_scope ON review_notes(run_id, scope)",
]


POSTGRES_SCHEMA = [
    """
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
    )
    """,
    """
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
    )
    """,
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_notes (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        scope TEXT NOT NULL,
        module TEXT,
        result_id BIGINT,
        dashboard_id TEXT,
        author TEXT NOT NULL,
        note TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_lock (
        name TEXT PRIMARY KEY,
        active_run_id TEXT,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state)",
    "CREATE INDEX IF NOT EXISTS idx_results_run_module_site ON results(run_id, module, site)",
    "CREATE INDEX IF NOT EXISTS idx_notes_run_scope ON review_notes(run_id, scope)",
]
