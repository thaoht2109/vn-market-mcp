-- AI-written "Nhận định" paragraph per ticker, written once by Hermes and replayed
-- while the data it was based on is unchanged (mcp_server/tools/stock_report.py fingerprint).
CREATE TABLE report_commentary (
  ticker           TEXT PRIMARY KEY,
  template_version TEXT NOT NULL,
  fingerprint      TEXT NOT NULL,
  run_id           TEXT NOT NULL,
  commentary       TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
