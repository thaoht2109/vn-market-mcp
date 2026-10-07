-- Good/neutral/bad judgment of one headline for one ticker, made by the Hermes chat LLM through the
-- judge_news tool (mcp_server/tools/stock_report.py). Feeds the chat report's reference score only
-- (news_events), never the official label. First judgment wins (INSERT ... ON CONFLICT DO NOTHING):
-- every user then sees the same score, and a later chat can't overwrite it. judged_by = VNMCP_USER_ID
-- of the profile that judged ('shared' outside a personal chat), to trace or reset a bad judgment.
-- No FK to news_items (partitioned, dropped by month): a judgment of a purged headline is just unused.
CREATE TABLE news_judgments (
  news_id    BIGINT NOT NULL,
  ticker     TEXT NOT NULL,
  sentiment  SMALLINT NOT NULL CHECK (sentiment IN (-1, 0, 1)),
  reason     TEXT NOT NULL,
  judged_by  TEXT NOT NULL,
  judged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (news_id, ticker)
);
