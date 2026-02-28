#!/usr/bin/env python3
"""Report win-rate for Bybit SR signal trial."""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta

WORKSPACE = "/root/.openclaw/workspace"
RESULTS = os.path.join(WORKSPACE, "memory", "bybit_sr_signal_results.jsonl")


def parse_ts(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    if not os.path.exists(RESULTS):
        return 0

    hours = float(os.environ.get("BYBIT_SR_REPORT_HOURS", "24"))
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    total = 0
    filled = 0
    wins = 0

    with open(RESULTS, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            ts = parse_ts(r.get("resolved_ts") or "")
            if ts and ts < since:
                continue
            total += 1
            if r.get("outcome") == "unfilled":
                continue
            filled += 1
            wins += int(r.get("win") or 0)

    if total == 0:
        return 0

    winrate = (wins / filled) if filled else 0.0

    out = []
    out.append(f"Bybit SR SIGNAL PAPER (last {hours:g}h)")
    out.append(f"Signals resolved: {total}")
    out.append(f"Filled: {filled}")
    out.append(f"Win-rate (filled only): {winrate:.1%} ({wins}/{filled})")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
