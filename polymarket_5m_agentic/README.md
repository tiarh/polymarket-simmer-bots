# Polymarket 5‑Min BTC “Arb” Agentic System (Blueprint + Scaffold)

This repository is a **production-style scaffold** for an agentic trading system aimed at Polymarket BTC 5‑minute markets.

> Note: I can’t see the attached screenshots via vision right now due to image-model quota limits. I built this from the spec text you provided in chat (layers, latency budget, risk controls, stack). If anything in the screenshots differs, paste the text and I’ll align it.

## Goals (from spec)
- Win rate: >70%
- Avg profit/trade: >$15
- Max drawdown: <5%
- Sharpe: >2.5

### Latency budget
- Data ingestion <100ms
- Signal generation <200ms
- Order execution <500ms
- End-to-end <1000ms

### Risk controls
- Max position: $50,000
- Max concurrent: 5 positions
- Daily loss limit: -$2,000
- Correlation threshold: 0.7
- Liquidity minimum: $10,000

## Architecture (5 layers)

### Layer 0 — Data ingestion
- Polymarket CLOB (book/trades/markets)
- CEX price feeds (Binance/Bybit/OKX websockets)
- Chainlink (optional) oracle reference
- Kafka (or Redpanda) event bus
- TimescaleDB (Postgres) time-series (ticks, OHLC, spreads)
- Redis for fast state + rate limiting + locks

### Layer 1 — Research agents (via LangGraph)
- Spread detector (CEX vs PM)
- Latency arb detector (lag estimation, lead/lag)
- Liquidity scanner (depth + slippage model)
- Bayesian fusion / synthesis node

### Layer 2 — Signal generation
- Alpha signal (YES/NO + confidence)
- Backtest validator (rolling evaluation)
- Risk filter (liquidity, correlation, limits)

### Layer 3 — Portfolio & risk management
- Position sizing (Kelly-capped)
- Exposure tracking, concurrency cap
- Tail-risk and platform-risk checks (gas, CLOB uptime)

### Layer 4 — Execution
- Maker/taker selection (rebate aware)
- Orderbook sniper (spread capture)
- Fill monitor + cancel/replace
- Optional hedge module (if you hedge off-platform)

## Repo layout
- `services/ingest` — feed collectors, normalizers, bus publisher
- `services/orchestrator` — LangGraph coordinator
- `services/signals` — signal builder + validator
- `services/risk` — portfolio/risk gate
- `services/exec` — execution engine (Polymarket CLOB)
- `libs/*` — shared libraries
- `infra/docker` — local docker-compose stack
- `infra/aws-sam` — Lambda packaging template (optional)

## Quick start (local)
```bash
cd infra/docker
docker compose up -d

# create venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# run (in separate shells)
python -m services.ingest.main
python -m services.orchestrator.main
```

## Safety & disclaimers
This is **engineering scaffolding**, not financial advice. Trading fast markets carries significant risk (fees, slippage, outages). Start in paper mode and enforce limits.
