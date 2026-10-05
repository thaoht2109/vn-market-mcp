CREATE TABLE prediction_outcomes (
  prediction_id BIGINT NOT NULL REFERENCES predictions(id),
  horizon_days INTEGER NOT NULL,
  graded_at TIMESTAMPTZ NOT NULL,
  filled BOOLEAN NOT NULL,
  entry_price NUMERIC,
  exit_reason TEXT NOT NULL,
  ret NUMERIC,
  excess_vs_vn30 NUMERIC,
  thesis_status TEXT,
  PRIMARY KEY (prediction_id, horizon_days)
);

CREATE TABLE retention_log (
  id BIGSERIAL PRIMARY KEY,
  run_at TIMESTAMPTZ DEFAULT now(),
  object_name TEXT NOT NULL,
  action TEXT NOT NULL,
  rows_affected BIGINT NOT NULL,
  archive_ref TEXT,
  dry_run BOOLEAN NOT NULL
);
