-- cafebiz writes pubDate with a "+07" offset that feedparser could not read, so each item got its fetch time as
-- published_at and was stored again once a week had passed (outside the URL dedup window). Keep the first copy.
DELETE FROM news_items n
USING news_items o
WHERE n.source = 'cafebiz_vi_mo' AND o.source = n.source AND o.url_hash = n.url_hash AND o.published_at < n.published_at;
