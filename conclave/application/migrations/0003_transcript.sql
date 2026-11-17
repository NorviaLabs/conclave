-- Agent conversations saved with their actions, one message per row and
-- appended as the conversation grows, instead of the whole conversation being
-- rewritten with every write. A saved frame refers to its first N messages.
CREATE TABLE IF NOT EXISTS studio_run_transcript (
    run_id TEXT NOT NULL REFERENCES studio_runs(id),
    stage TEXT NOT NULL,
    log TEXT NOT NULL,
    position INTEGER NOT NULL,
    message BLOB NOT NULL,
    PRIMARY KEY (run_id, stage, log, position)
);
