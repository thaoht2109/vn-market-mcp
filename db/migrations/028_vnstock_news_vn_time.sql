-- vnstock's public_date is Vietnam local time without an offset; it was stored as UTC, 7 h late.
-- Shift the rows ingested before the fix so re-fetched items match them instead of duplicating.
UPDATE news_items SET published_at = published_at - interval '7 hours' WHERE source = 'vnstock';
