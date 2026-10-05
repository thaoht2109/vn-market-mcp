-- Breadth + liquidity of the VN30 universe next to the VN-Index trend, so a report can
-- tell "index up on 3 heavyweights" from "broad advance". liquidity_ratio is NULL while
-- the session is still open (today's traded value is partial).
ALTER TABLE market_regime_daily
  ADD COLUMN advancers       INTEGER,
  ADD COLUMN decliners       INTEGER,
  ADD COLUMN pct_above_ma50  NUMERIC,   -- share of VN30 tickers closing above their 50-day mean (0-1)
  ADD COLUMN liquidity_ratio NUMERIC;   -- VN30 traded value / mean of the previous 20 sessions
