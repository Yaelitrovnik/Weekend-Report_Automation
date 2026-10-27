CREATE TABLE IF NOT EXISTS manual_db_reviews (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    display_name TEXT NOT NULL
        CHECK (length(trim(display_name)) > 0),
    script_path TEXT NOT NULL
        CHECK (length(trim(script_path)) > 0),
    result TEXT NOT NULL
        CHECK (result IN ('PASS', 'FAIL', 'NOT RUN')),
    comment TEXT NOT NULL
        CHECK (length(trim(comment)) > 0),
    reviewer TEXT NOT NULL
        CHECK (length(trim(reviewer)) > 0),
    reviewed_at TIMESTAMPTZ NOT NULL
);