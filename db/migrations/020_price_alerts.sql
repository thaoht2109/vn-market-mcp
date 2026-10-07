-- Price alerts on the official verdict's levels (pipeline/price_alerts.py).
-- Edge-triggered: alert_state remembers whether the last price already met each condition, so a
-- price sitting inside a zone alerts once, not every check.
CREATE TABLE alert_state (
  ticker      TEXT NOT NULL,
  condition   TEXT NOT NULL,          -- entry_zone | stop_loss | target
  kind        TEXT NOT NULL,          -- touched (in-session price) | confirmed (closing price)
  run_id      TEXT NOT NULL,          -- official run the levels come from; a new one re-initialises the row
  inside      BOOLEAN NOT NULL,
  last_price  NUMERIC NOT NULL,
  checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, condition, kind)
);

-- One row per user per fired condition; the user's Hermes cron prints and marks undelivered rows.
-- The UNIQUE key caps each condition at one alert per session per user, whatever the code does.
CREATE TABLE user_alerts (
  id            BIGSERIAL PRIMARY KEY,
  user_id       TEXT NOT NULL,
  ticker        TEXT NOT NULL,
  run_id        TEXT NOT NULL,
  condition     TEXT NOT NULL,
  kind          TEXT NOT NULL,
  session_date  DATE NOT NULL,
  price         NUMERIC NOT NULL,
  message       TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  delivered_at  TIMESTAMPTZ,
  UNIQUE (user_id, run_id, condition, kind, session_date)
);
CREATE INDEX user_alerts_pending_idx ON user_alerts (user_id) WHERE delivered_at IS NULL;

-- Opt-out from chat ("tắt cảnh báo"). ticker '*' = every ticker; a per-ticker row overrides it.
-- pipeline_rw has no DELETE, so turning alerts back on flips `enabled` instead of removing rows.
CREATE TABLE alert_prefs (
  user_id     TEXT NOT NULL,
  ticker      TEXT NOT NULL DEFAULT '*',
  enabled     BOOLEAN NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, ticker)
);
