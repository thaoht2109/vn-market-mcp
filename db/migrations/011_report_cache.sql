-- Latest finished Hermes report per ticker, reused while the underlying data
-- (see mcp_server/tools/report_cache.py fingerprint) is unchanged.
CREATE TABLE report_cache (
  ticker           TEXT PRIMARY KEY,
  template_version TEXT NOT NULL,
  fingerprint      TEXT NOT NULL,
  run_id           TEXT NOT NULL,
  report_text      TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
