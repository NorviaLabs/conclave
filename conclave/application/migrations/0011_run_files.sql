-- Files a run stored from its steps' outputs: Markdown, CSV or JSON, written by
-- the host. The bytes live beside the database under run-files/<run>/<id>; this
-- row is what a download is checked against (size and SHA-256).
CREATE TABLE IF NOT EXISTS studio_run_files (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES studio_runs(id),
    stage TEXT NOT NULL,
    output TEXT NOT NULL,
    round INTEGER NOT NULL,
    name TEXT NOT NULL,
    format TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    preview TEXT NOT NULL,
    lines INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS studio_run_files_run ON studio_run_files (run_id, stage);
