-- Official numeric macro series (docs/superpowers/specs/2026-10-06-sbv-spike-findings.md).
-- One row per (indicator, period, source); a re-fetch of the same period overwrites the value.
CREATE TABLE macro_indicators (
  indicator    TEXT NOT NULL,          -- e.g. 'usd_vnd_central', 'interbank_overnight', 'refinancing_rate'
  period       DATE NOT NULL,          -- the date the value applies to, as published by the source
  value        NUMERIC NOT NULL,
  unit         TEXT NOT NULL,          -- 'VND' | '%/năm'
  source       TEXT NOT NULL,          -- 'sbv'
  source_url   TEXT NOT NULL,
  fetched_at   TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (indicator, period, source)
);
