-- TimescaleDB init
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS ticks (
  ts TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL,         -- binance|bybit|okx|polymarket
  symbol TEXT NOT NULL,         -- BTCUSDT|PM:market_id
  bid NUMERIC,
  ask NUMERIC,
  last NUMERIC,
  extra JSONB,
  PRIMARY KEY (ts, source, symbol)
);
SELECT create_hypertable('ticks', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS spreads (
  ts TIMESTAMPTZ NOT NULL,
  market_id TEXT NOT NULL,
  cex_mid NUMERIC NOT NULL,
  pm_yes NUMERIC,
  pm_no NUMERIC,
  edge_yes_bps NUMERIC,
  edge_no_bps NUMERIC,
  lag_ms INTEGER,
  extra JSONB,
  PRIMARY KEY (ts, market_id)
);
SELECT create_hypertable('spreads', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS orders (
  ts TIMESTAMPTZ NOT NULL,
  venue TEXT NOT NULL,          -- polymarket
  order_id TEXT NOT NULL,
  market_id TEXT NOT NULL,
  side TEXT NOT NULL,           -- yes|no
  price NUMERIC NOT NULL,
  size_usd NUMERIC NOT NULL,
  status TEXT NOT NULL,         -- new|partial|filled|canceled|rejected
  extra JSONB,
  PRIMARY KEY (order_id)
);
