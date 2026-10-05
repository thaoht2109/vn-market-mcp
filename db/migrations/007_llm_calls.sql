CREATE TABLE llm_calls (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT REFERENCES runs(run_id),
  role TEXT NOT NULL,
  model_id TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  prompt_version TEXT,
  experiment_id TEXT,
  is_shadow BOOLEAN DEFAULT false,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cached_tokens INTEGER,
  cost_usd NUMERIC,
  latency_ms INTEGER,
  schema_valid BOOLEAN,
  error TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
