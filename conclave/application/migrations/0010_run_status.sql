-- Runs by status: what is still going is a handful among many finished runs,
-- and finding it (an event's active-run limit, the scheduler) should not read
-- every run's stored request.
CREATE INDEX IF NOT EXISTS studio_runs_status ON studio_runs (status);
