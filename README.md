# polymarket-simmer-bots

A collection of **paper-first** trading bots + monitoring utilities built around:

- **OpenClaw** (agent runtime + Telegram messaging + automation)
- **Simmer SDK** (execution + portfolio/positions APIs for Polymarket)
- **systemd user services/timers** (reliable scheduling on a VPS)

This repo was developed to run multiple strategies safely with:
- strict **risk caps** (max position, max concurrent, daily loss limits)
- **journaling** (JSONL + CSV)
- **resolvers** + **daily reports** (win-rate / PnL summaries)
- **push notifications** (OPENED/CLOSED + periodic PnL)

## What’s inside

### Strategies / bots
- **Polymarket weather trader** (LIVE-capable, risk controlled)
- **NO-grinder** (now supports *exit-only mode* to manage closes without new entries)
- **BTC 15m fast-market arb (paper)** with discovery + resolver + daily report
- **Bybit BTCUSDT perp S/R copilot (signal-only)**
  - sends Telegram signals with Entry/SL/TP + TradingView link
  - optional TradingView screenshot automation

### Monitoring
- Position watcher that emits **OPENED/CLOSED** + **hourly PnL** while positions are open.

## Tech stack

- **Python** (runtime venv: `weather-env`, async/sync utilities)
- **OpenClaw** agent runtime
- **Simmer** execution/portfolio APIs (via `simmer_sdk`)
- **systemd (user)** timers + services
- **Playwright + Chromium** (optional: screenshot automation for TradingView)

### Model / LLM
OpenClaw is configured to use an OpenAI model (e.g. **GPT (ChatGPT Plus / OpenAI)**) for orchestration and ops. Trading decisions themselves are implemented as deterministic code with explicit risk rules (LLM is not in the execution hot-path).

## Safety defaults

- Bots are designed **paper-first**. Live trading is always behind explicit env flags.
- Secrets are loaded via `EnvironmentFile=.../secrets/*.env` and **must never be committed**.

## Quick start (paper)

1) Create a Python venv (example):
```bash
python3 -m venv weather-env
source weather-env/bin/activate
pip install -r requirements.txt  # (if/when you add one)
```

2) Add secrets (DO NOT COMMIT):
```bash
mkdir -p secrets
# Simmer
nano secrets/simmer.env
```

3) Install systemd user units (templates live in `deploy/systemd/`):
```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now <your-timer>.timer
```

## Notes on publishing

This repo contains operational scripts. Before publishing to a public GitHub repository, **sanitize**:
- remove `secrets/`, `memory/`, and any local exports/logs
- remove chat IDs, tokens, IPs, and router configs
- keep only `deploy/systemd/*` templates, scripts, and skills code

---

If you want a public version, create a separate branch (e.g. `public-sanitized`) and only include non-sensitive files.
