# polymarket-btc-15m-arb

Paper-first 15-minute BTC up/down arbitrage-style signaler/executor using Simmer SDK + Binance spot feed.

## What it does
- Ingests BTCUSDT price stream (Binance WS) + Simmer market prices/orderbook (via SDK endpoints).
- Computes simple fair-value + edge + confidence.
- Paper mode by default: logs trade intents + simulated fills.
- Journals every decision (TRADE/SKIP) to JSONL + CSV.

## Safety
- Defaults to PAPER (no live orders) unless `SIMMER_BTC_ARB_LIVE=1`.
- Enforces Polymarket minimum order shares via `SIMMER_MIN_SHARES` (default 5) with bump cap `SIMMER_BTC_ARB_MAX_BUMP_USD`.
- Daily loss / max concurrent / max position caps.

## Run
```bash
set -a; source /root/.openclaw/workspace/secrets/simmer.env; set +a
python3 skills/polymarket-btc-15m-arb/btc_arb.py -q
```

## Env
- `SIMMER_BTC_ARB_LIVE` (0/1)
- `SIMMER_BTC_ARB_MAX_POSITION_USD` (default 200)
- `SIMMER_BTC_ARB_MAX_CONCURRENT` (default 2)
- `SIMMER_BTC_ARB_DAILY_LOSS_LIMIT_USD` (default 50)
- `SIMMER_BTC_ARB_EDGE_MIN` (default 0.02)
- `SIMMER_BTC_ARB_CONF_MIN` (default 0.60)
- `SIMMER_BTC_ARB_LIQUIDITY_MIN_USD` (default 1000)  # start low for paper
- `SIMMER_MIN_SHARES` (default 5)
- `SIMMER_BTC_ARB_MAX_BUMP_USD` (default 5)
- `SIMMER_BTC_ARB_MARKET_SLUG_PREFIX` (default `btc-updown-15m`)
- `SIMMER_BTC_ARB_POLL_SECS` (default 10)

## Outputs
- `memory/btc_15m_arb_journal.jsonl`
- `memory/btc_15m_arb_journal.csv`
