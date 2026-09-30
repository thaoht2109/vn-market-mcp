CREATE TABLE prices_daily (
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  trade_date DATE NOT NULL,
  open NUMERIC NOT NULL,
  high NUMERIC NOT NULL,
  low NUMERIC NOT NULL,
  close NUMERIC NOT NULL,
  volume BIGINT NOT NULL,
  value NUMERIC,
  source TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE price_adjustments (
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  ex_date DATE NOT NULL,
  kind TEXT NOT NULL,
  factor NUMERIC NOT NULL,
  PRIMARY KEY (ticker, ex_date, kind)
);

CREATE TABLE foreign_flow_daily (
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  trade_date DATE NOT NULL,
  buy_value NUMERIC,
  sell_value NUMERIC,
  net_value NUMERIC,
  room_left NUMERIC,
  source TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE fundamentals_quarterly (
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  period TEXT NOT NULL,
  report_type TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  metrics JSONB NOT NULL,
  published_date DATE,
  source TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (ticker, period, report_type, version)
);

CREATE TABLE corporate_events (
  id BIGSERIAL PRIMARY KEY,
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  event_type TEXT NOT NULL,
  event_date DATE NOT NULL,
  payload JSONB,
  source_url TEXT,
  fetched_at TIMESTAMPTZ NOT NULL
);
