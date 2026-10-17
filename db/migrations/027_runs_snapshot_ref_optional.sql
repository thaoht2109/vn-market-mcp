-- A run's snapshot lives in runs.snapshot (026); new runs write no file, so there is no path to record.
-- Rows from before keep their snapshot_ref as history.
ALTER TABLE runs ALTER COLUMN snapshot_ref DROP NOT NULL;
