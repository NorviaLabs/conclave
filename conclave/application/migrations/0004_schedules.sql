-- Scheduled applications: each schedule's next time and owner, and every
-- scheduled time's outcome. The slot's primary key is the exactly-once claim:
-- a time is taken by inserting its row together with its run.
CREATE TABLE IF NOT EXISTS studio_schedules (
    application_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    time_zone TEXT NOT NULL,
    anchor TEXT NOT NULL,
    next_at TEXT,
    owner TEXT,
    owner_since TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    paused INTEGER NOT NULL DEFAULT 0,
    paused_by TEXT,
    paused_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS studio_schedule_slots (
    application_id TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    outcome TEXT NOT NULL,
    run_id TEXT REFERENCES studio_runs(id),
    detail TEXT,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (application_id, scheduled_for)
);
