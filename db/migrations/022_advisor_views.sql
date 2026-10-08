-- The advisor's own call on a ticker, written by a Hermes sub-agent that sees the report's data but
-- not the system label (mcp_server/tools/advisor.py). One per (user, run): first one wins and is
-- replayed. code_label = the label that user saw for that run, stored to compare the two later on
-- forward returns (ops/backtest_score.py). Never changes the official label or predictions.
-- No FK to runs: retention may drop old runs, the scorecard only needs ticker + created_at.
CREATE TABLE advisor_views (
  user_id    TEXT NOT NULL,
  run_id     TEXT NOT NULL,
  ticker     TEXT NOT NULL,
  holding    BOOLEAN NOT NULL,
  stance     TEXT NOT NULL CHECK (stance IN ('buy_accumulate', 'watch', 'stay_out', 'hold', 'reduce_exit')),
  code_label TEXT,
  advice     TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, run_id)
);
CREATE INDEX advisor_views_ticker ON advisor_views (ticker, created_at);
