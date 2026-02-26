#!/usr/bin/env python3
"""Summarize BTC 15m arb paper performance from results file."""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta

WORKSPACE = "/root/.openclaw/workspace"
RESULTS = os.path.join(WORKSPACE, "memory", "btc_15m_arb_results.jsonl")


def parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    if not os.path.exists(RESULTS):
        return 0

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    n = 0
    wins = 0
    pnl = 0.0
    fees = 0.0

    with open(RESULTS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue

            ts = parse_ts(r.get("resolved_ts") or "")
            if ts and ts < since:
                continue

            n += 1
            wins += 1 if float(r.get("win") or 0) >= 1 else 0
            pnl += float(r.get("pnl_net") or 0)
            fees += float(r.get("fee") or 0)

    if n == 0:
        return 0

    winrate = wins / n if n else 0
    out = []
    out.append("BTC 15m ARB PAPER (last 24h)")
    out.append(f"Trades resolved: {n}")
    out.append(f"Win-rate: {winrate:.1%} ({wins}/{n})")
    out.append(f"Net PnL: ${pnl:,.2f}")
    out.append(f"Fees est.: ${fees:,.2f}")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
