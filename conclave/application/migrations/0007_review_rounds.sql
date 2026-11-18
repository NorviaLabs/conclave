-- A review may be asked more than once: each time its reviewer asks for
-- changes, the reviewed steps run again and the review is asked again, as a new
-- round. Rebuilds studio_run_reviews without UNIQUE (run_id, stage), keeping
-- every existing review (as round 1) in its original order.
-- Applied by RunRepository only while the old constraint is still there.
CREATE TABLE studio_run_reviews_rounds (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES studio_runs(id),
    stage TEXT NOT NULL,
    request TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewer TEXT,
    output TEXT,
    submitted_at TEXT,
    round INTEGER NOT NULL DEFAULT 1
);
INSERT INTO studio_run_reviews_rounds (id, run_id, stage, request, status, reviewer, output, submitted_at)
    SELECT id, run_id, stage, request, status, reviewer, output, submitted_at FROM studio_run_reviews ORDER BY rowid;
DROP TABLE studio_run_reviews;
ALTER TABLE studio_run_reviews_rounds RENAME TO studio_run_reviews;
CREATE INDEX IF NOT EXISTS studio_run_reviews_stage ON studio_run_reviews (run_id, stage);
