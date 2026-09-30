CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  tickers TEXT[] NOT NULL,
  style TEXT NOT NULL,
  depth TEXT NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  snapshot_ref TEXT NOT NULL,
  report_md TEXT,
  warnings JSONB,
  cost_tokens INTEGER,
  config_hash TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE theses (
  id BIGSERIAL PRIMARY KEY,
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  version INTEGER NOT NULL,
  summary TEXT NOT NULL,
  pillars JSONB NOT NULL,
  invalidation_rules JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  created_by_run TEXT REFERENCES runs(run_id)
);

CREATE TABLE predictions (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT REFERENCES runs(run_id),
  source TEXT NOT NULL,
  ticker TEXT NOT NULL,
  trigger TEXT NOT NULL,
  thesis_id BIGINT REFERENCES theses(id),
  action_label TEXT NOT NULL,
  universe_tier TEXT NOT NULL DEFAULT 'A',
  holding_state TEXT DEFAULT 'unknown',
  coverage JSONB,
  config_hash TEXT,
  signal_type TEXT NOT NULL,
  entry_zone NUMRANGE,
  stop_loss NUMERIC,
  target NUMERIC,
  horizon_days INTEGER,
  confidence NUMERIC,
  created_at TIMESTAMPTZ DEFAULT now(),
  status TEXT DEFAULT 'open'
);

CREATE UNIQUE INDEX one_open_prediction
  ON predictions (ticker, action_label, COALESCE(thesis_id, 0)) WHERE status = 'open';
