CREATE TABLE positions (
  ticker TEXT PRIMARY KEY REFERENCES tickers(ticker),
  -- Deviation from the spec's literal §7.3 SQL: added `status`. Without it,
  -- "explicitly not holding" (/khonggiu) and "never declared" are both just
  -- a missing row, which contradicts the spec's own three-state prose in
  -- §5.8 ("chưa khai báo" = unknown vs. "đã gỡ bằng /khonggiu" = none).
  status TEXT NOT NULL DEFAULT 'holding',  -- holding | none
  avg_cost NUMERIC,
  declared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  declared_by TEXT NOT NULL
);

CREATE TABLE data_quality_log (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT,
  ticker TEXT,
  check_name TEXT NOT NULL,
  result TEXT NOT NULL,
  detail JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE watchlist_extra (
  ticker TEXT PRIMARY KEY REFERENCES tickers(ticker),
  confirmed_at TIMESTAMPTZ NOT NULL,
  added_by TEXT,
  last_interaction_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'active'
);
