-- Add durable human reviews without changing existing run/event tables.
CREATE TABLE IF NOT EXISTS studio_run_reviews (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES studio_runs(id),
    stage TEXT NOT NULL,
    request TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewer TEXT,
    output TEXT,
    submitted_at TEXT,
    UNIQUE (run_id, stage)
);
