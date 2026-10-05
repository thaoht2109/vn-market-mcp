-- One row per trading day: VN-Index state + the risk_on/risk_off regime label
-- that action_label() gates on. Written by the pipeline, read by Hermes via the
-- snapshot's "market" block, so VN-Index is fetched once, not once per ticker.
CREATE TABLE market_regime_daily (
  trade_date   DATE PRIMARY KEY,
  index_symbol TEXT NOT NULL,
  close        NUMERIC,
  change_pct   NUMERIC,            -- vs previous session close
  ma20         NUMERIC,
  ma50         NUMERIC,
  ma200        NUMERIC,
  rsi14        NUMERIC,
  trend        TEXT,               -- up | down | sideways (NULL = not enough history)
  regime       TEXT NOT NULL,      -- risk_on | risk_off
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
