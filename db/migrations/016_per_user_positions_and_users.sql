-- One position per (ticker, user): a shared row let one user's /giu overwrite another's.
ALTER TABLE positions DROP CONSTRAINT positions_pkey;
ALTER TABLE positions ADD PRIMARY KEY (ticker, declared_by);

-- Where the worker delivers a user's results: their own Hermes bot (its token is read from the
-- worker env var named here, never stored in the DB) and their chat.
CREATE TABLE users (
  user_id       TEXT PRIMARY KEY,
  chat_id       TEXT NOT NULL,
  bot_token_env TEXT NOT NULL DEFAULT 'TELEGRAM_BOT_TOKEN',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
