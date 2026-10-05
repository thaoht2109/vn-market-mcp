-- Partitioned by month per spec §7.3/§7.4: cheap to DETACH/DROP old
-- partitions during retention instead of a bulk DELETE on a huge table.
CREATE TABLE news_items (
  id BIGSERIAL,
  published_at TIMESTAMPTZ NOT NULL,
  tickers TEXT[] NOT NULL,
  source TEXT NOT NULL,
  url TEXT,
  url_hash TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT,
  sentiment NUMERIC,
  fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (id, published_at)
) PARTITION BY RANGE (published_at);

-- url_hash dedupes re-fetches of the same article across tickers/runs
-- (spec §5.7.3 "khử trùng lặp theo url_hash"); unique per partition since
-- a global unique index isn't allowed across range partitions on a column
-- that isn't the partition key.
CREATE UNIQUE INDEX news_items_url_hash_published_idx ON news_items (url_hash, published_at);

-- Seed partitions through the current quarter; ops/seed_market_data.py or a
-- retention job should create next month's partition ahead of time so
-- inserts never hit the "no partition for this date" error.
CREATE TABLE news_items_2026_09 PARTITION OF news_items
  FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE news_items_2026_10 PARTITION OF news_items
  FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE news_items_2026_11 PARTITION OF news_items
  FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE news_items_2026_12 PARTITION OF news_items
  FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
