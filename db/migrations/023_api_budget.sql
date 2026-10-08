-- One shared request budget per external API (providers/rate_limit.py): every vnstock call, from any
-- worker or MCP server, first reserves the next free slot here, so all processes together stay under
-- the vendor's per-minute limit instead of each one tripping it and losing its job's progress.
CREATE TABLE api_budget (
  name      TEXT PRIMARY KEY,
  next_slot TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO api_budget (name) VALUES ('vnstock');
