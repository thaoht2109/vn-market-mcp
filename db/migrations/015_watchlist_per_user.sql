-- One watchlist per chat user: the same ticker may be watched by several people.
-- Unwatch flips status to 'inactive' (pipeline_rw has no DELETE).
UPDATE watchlist_extra SET added_by = 'legacy' WHERE added_by IS NULL;
ALTER TABLE watchlist_extra DROP CONSTRAINT watchlist_extra_pkey;
ALTER TABLE watchlist_extra ALTER COLUMN added_by SET NOT NULL;
ALTER TABLE watchlist_extra ADD PRIMARY KEY (ticker, added_by);
