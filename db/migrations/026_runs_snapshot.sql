-- The run's snapshot stored with the run (it was only a JSON file under snapshots/, which pg_dump does not cover and
-- a second machine does not see). NULL = a run from before this column: readers fall back to the file at snapshot_ref
-- until ops/backfill_snapshots.py has loaded it.
ALTER TABLE runs ADD COLUMN snapshot JSONB;
