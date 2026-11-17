-- "This run is waiting for you": one notice per waiting point, written in the
-- same transaction as the event that made the run wait, and claimed before it
-- is sent, so no waiting point is ever told twice. And whose account sends an
-- application's notices (whoever last saved a change to what runs).
CREATE TABLE IF NOT EXISTS studio_run_notices (
    run_id TEXT NOT NULL REFERENCES studio_runs(id),
    sequence INTEGER NOT NULL,
    state TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
CREATE INDEX IF NOT EXISTS studio_run_notices_state ON studio_run_notices (state);
CREATE TABLE IF NOT EXISTS studio_notification_owners (
    application_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    owner_since TEXT NOT NULL
);
-- Who a run's notices are sent as: the notification owner when the run was
-- created, i.e. whoever last changed the words of the version it runs. Never
-- whoever owns the notifications later (security review NS1).
CREATE TABLE IF NOT EXISTS studio_run_senders (
    run_id TEXT PRIMARY KEY REFERENCES studio_runs(id),
    sender TEXT NOT NULL
);
-- The hourly limit counts the last hour's notices; this keeps that count from
-- reading every notice ever sent (performance review P2).
CREATE INDEX IF NOT EXISTS studio_run_notices_sent_at ON studio_run_notices (updated_at);
