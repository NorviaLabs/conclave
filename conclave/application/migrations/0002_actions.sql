-- The action journal: each external write recorded before it is sent, and the
-- agent progress a run continues from. Added without changing existing tables.
CREATE TABLE IF NOT EXISTS studio_run_actions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES studio_runs(id),
    stage TEXT NOT NULL,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    approval TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    checkable INTEGER NOT NULL DEFAULT 0,
    call BLOB,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    reason TEXT,
    check_result TEXT,
    check_summary TEXT,
    check_detail BLOB,
    checked_at TEXT,
    settled_by TEXT,
    settled_kind TEXT,
    settled_note BLOB,
    settled_at TEXT,
    final INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS studio_run_actions_run ON studio_run_actions (run_id);
CREATE TABLE IF NOT EXISTS studio_run_progress (
    run_id TEXT NOT NULL REFERENCES studio_runs(id),
    stage TEXT NOT NULL,
    frames BLOB NOT NULL,
    budget TEXT NOT NULL,
    output BLOB,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, stage)
);
