CREATE TABLE tickers (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  exchange TEXT,
  sector TEXT,
  industry_group TEXT,
  listed_date DATE,
  delisted_date DATE
);

CREATE TABLE index_membership (
  index_code TEXT NOT NULL,
  ticker TEXT NOT NULL REFERENCES tickers(ticker),
  valid_from DATE NOT NULL,
  valid_to DATE,
  PRIMARY KEY (index_code, ticker, valid_from)
);

CREATE TABLE trading_calendar (
  trade_date DATE PRIMARY KEY,
  is_trading_day BOOLEAN NOT NULL,
  note TEXT
);
