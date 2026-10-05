CREATE TABLE jobs (
  id           BIGSERIAL PRIMARY KEY,
  job_key      TEXT NOT NULL UNIQUE,  -- dedupe key, e.g. "on_demand:HPG:<ms>" or "scheduled_post:2026-09-29"
  job_type     TEXT NOT NULL,         -- on_demand | scheduled_pre | scheduled_post
  ticker       TEXT NOT NULL,
  style        TEXT NOT NULL DEFAULT 'long',
  depth        TEXT NOT NULL DEFAULT 'quick',
  requested_by TEXT,                  -- chat user id, or 'cron'
  status       TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed | expired
  attempts     INTEGER NOT NULL DEFAULT 0,
  run_id       TEXT,                  -- filled in once pipeline.run_analysis returns
  error        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at   TIMESTAMPTZ,
  finished_at  TIMESTAMPTZ
);

CREATE INDEX jobs_status_created_idx ON jobs (status, created_at);
