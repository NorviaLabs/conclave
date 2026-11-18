-- Who answered each review, and how. A review may need more than one person:
-- each person's answer is one row, and a review is submitted once it has the
-- answers it needs (or the first rejection). One answer per person per review.
CREATE TABLE IF NOT EXISTS studio_run_review_answers (
    review_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES studio_runs(id),
    reviewer TEXT NOT NULL,
    reviewer_key TEXT NOT NULL,
    output TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    PRIMARY KEY (review_id, reviewer_key)
);
CREATE INDEX IF NOT EXISTS studio_run_review_answers_run ON studio_run_review_answers (run_id);
