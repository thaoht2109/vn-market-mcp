-- Phase 1 of news collection (docs/superpowers/specs/2026-10-06-news-collection-phase1-design.md).
-- Existing rows (vnstock headlines) keep NULL in the new columns and read as "kept".
ALTER TABLE news_items
  ADD COLUMN stream        TEXT,                       -- 'A' macro | 'B' company | NULL (vnstock)
  ADD COLUMN filter_status TEXT,                       -- 'kept' | 'dropped' | NULL (vnstock: treat as kept)
  ADD COLUMN filter_reason TEXT,                       -- 'exclude:<phrase>' | 'no_keyword' | 'no_ticker' | 'duplicate_title:<url_hash>'
  ADD COLUMN pillars       TEXT[] NOT NULL DEFAULT '{}';

-- Freshness per news source. Silence must never read as "no news": readers and alerts use this table.
CREATE TABLE source_health (
  source               TEXT PRIMARY KEY,               -- `name` in config/news_sources.yaml, or 'vnstock_news'
  last_ok_at           TIMESTAMPTZ,
  last_item_at         TIMESTAMPTZ,                    -- newest published_at received (never later than the fetch time)
  last_error           TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  alerted_at           TIMESTAMPTZ,                    -- set when an ops alert was sent; cleared on real recovery
  first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now()  -- silence clock for a source that never produced an item
);
