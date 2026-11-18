-- Runs started by an event. Each application in event mode may have one
-- incoming address (an unguessable endpoint) with a signing secret, sealed
-- with the host key when there is one. A claim holds an event's identity for
-- seven days, so the same event starts one run; the log keeps the latest
-- events an application received and what became of each.
CREATE TABLE IF NOT EXISTS studio_event_endpoints (
    application_id TEXT PRIMARY KEY,
    endpoint_id TEXT NOT NULL UNIQUE,
    secret TEXT NOT NULL,
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL,
    rotated_at TEXT
);
CREATE TABLE IF NOT EXISTS studio_event_claims (
    application_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (application_id, event_key)
);
CREATE INDEX IF NOT EXISTS studio_event_claims_received ON studio_event_claims (received_at);
CREATE TABLE IF NOT EXISTS studio_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    received_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    run_id TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS studio_event_log_application ON studio_event_log (application_id, id);
