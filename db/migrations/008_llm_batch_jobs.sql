CREATE TABLE llm_batch_jobs (
  id BIGSERIAL PRIMARY KEY,
  provider_batch_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  role TEXT NOT NULL,
  model_key TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'submitted',  -- submitted | in_progress | completed | failed | expired | canceled
  item_count INTEGER NOT NULL,
  submitted_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ,
  UNIQUE (provider, provider_batch_id)
);

-- One row per item inside a batch, keyed by the caller-supplied custom_id,
-- so results can be joined back to the run/prediction that requested them.
CREATE TABLE llm_batch_items (
  id BIGSERIAL PRIMARY KEY,
  batch_job_id BIGINT NOT NULL REFERENCES llm_batch_jobs(id),
  custom_id TEXT NOT NULL,
  run_id TEXT REFERENCES runs(run_id),
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | succeeded | errored
  output JSONB,
  error TEXT,
  UNIQUE (batch_job_id, custom_id)
);
