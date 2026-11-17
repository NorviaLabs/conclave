-- Whom each run acts as: the person who started it, the schedule's owner, or
-- the hook connection's owner. Approvals and decisions on the run are theirs,
-- so the host's own principal is never a stand-in for a person.
CREATE TABLE IF NOT EXISTS studio_run_actors (
    run_id TEXT PRIMARY KEY REFERENCES studio_runs(id),
    actor TEXT NOT NULL
);
