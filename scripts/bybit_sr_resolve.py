#!/usr/bin/env python3
"""Resolve Bybit S/R SIGNAL journal into paper outcomes (win/loss/unfilled).

Rules (simple, deterministic):
- For each journal row type=signal, look ahead N candles (default 24 = 2h on 5m).
- Consider entry filled if future candle range crosses entry.
  - LONG: low <= entry <= high
  - SHORT: low <= entry <= high
- After filled, determine which hits first: SL or TP.
  - LONG: SL hit if low <= SL, TP hit if high >= TP
  - SHORT: SL hit if high >= SL, TP hit if low <= TP
- If both hit same candle, assume WORST (loss) to be conservative.

Reads:
  memory/bybit_sr_signal_journal.jsonl
Writes:
  memory/bybit_sr_signal_results.jsonl + .csv
State:
  memory/bybit_sr_signal_resolve_state.json

Note: This is PAPER evaluation; no fees/slippage/funding unless added later.
"""

from __future__ import annotations

import os
import json
import csv
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

WORKSPACE = "/root/.openclaw/workspace"
JOURNAL = os.path.join(WORKSPACE, "memory", "bybit_sr_signal_journal.jsonl")
OUT_JSONL = os.path.join(WORKSPACE, "memory", "bybit_sr_signal_results.jsonl")
OUT_CSV = os.path.join(WORKSPACE, "memory", "bybit_sr_signal_results.csv")
STATE = os.path.join(WORKSPACE, "memory", "bybit_sr_signal_resolve_state.json")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_json(url: str, timeout: int = 15) -> Any:
    req = Request(url, headers={"User-Agent": "bybit-sr-resolver/0.1"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"resolved": {}}


def save_state(st: Dict[str, Any]) -> None:
    ensure_parent(STATE)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


def append_out(row: Dict[str, Any]) -> None:
    ensure_parent(OUT_JSONL)
    ensure_parent(OUT_CSV)
    with open(OUT_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    exists = os.path.exists(OUT_CSV)
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            w.writeheader()
        w.writerow(row)


def fetch_klines(symbol: str, interval: str, limit: int, start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> List[List[str]]:
    # Bybit returns most-recent first; we sort asc.
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    if start_ms is not None:
        url += f"&start={int(start_ms)}"
    if end_ms is not None:
        url += f"&end={int(end_ms)}"
    data = http_json(url, timeout=15)
    lst = (((data.get("result") or {}).get("list")) or [])
    return sorted(lst, key=lambda r: int(r[0]))


def main() -> int:
    if not os.path.exists(JOURNAL):
        return 0

    lookahead = int(os.environ.get("BYBIT_RESOLVE_LOOKAHEAD_CANDLES", "24"))

    st = load_state()
    resolved = st.get("resolved", {})

    # Load all signals
    signals: List[Dict[str, Any]] = []
    with open(JOURNAL, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("type") != "signal":
                continue
            signals.append(r)

    if not signals:
        return 0

    symbol = signals[-1].get("symbol", "BTCUSDT")
    interval = str(signals[-1].get("interval", "5"))

    # interval minutes -> ms
    int_ms = int(float(interval) * 60_000)

    new = 0
    for s in signals[-200:]:
        key = f"{s.get('ts')}:{s.get('side')}:{s.get('entry')}:{s.get('sl')}:{s.get('tp')}"
        if key in resolved:
            continue

        side = s.get("side")
        entry = float(s.get("entry"))
        sl = float(s.get("sl"))
        tp = float(s.get("tp"))

        # Fetch candles anchored around the signal candle timestamp
        sig_ms = int(s.get("candle_ts") or 0)
        # If missing candle_ts, fall back to end window.
        if sig_ms <= 0:
            sig_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - int_ms * lookahead

        start_ms = sig_ms
        end_ms = sig_ms + int_ms * (lookahead + 2)

        kl = fetch_klines(symbol, interval, limit=min(200, lookahead + 50), start_ms=start_ms, end_ms=end_ms)
        candles = []
        for r in kl:
            ts = int(r[0])
            o_ = float(r[1]); h_ = float(r[2]); l_ = float(r[3]); c_ = float(r[4])
            candles.append((ts, o_, h_, l_, c_))

        filled_idx: Optional[int] = None
        outcome = "unfilled"

        for i in range(len(candles)):
            _ts, _o, hi, lo, _c = candles[i]
            if lo <= entry <= hi:
                filled_idx = i
                break

        if filled_idx is not None:
            for j in range(filled_idx, len(candles)):
                _ts, _o, hi, lo, _c = candles[j]
                if side == "LONG":
                    hit_sl = lo <= sl
                    hit_tp = hi >= tp
                else:
                    hit_sl = hi >= sl
                    hit_tp = lo <= tp

                if hit_sl and hit_tp:
                    outcome = "loss"  # conservative
                    break
                if hit_sl:
                    outcome = "loss"
                    break
                if hit_tp:
                    outcome = "win"
                    break

        out = {
            "resolved_ts": utc_now_iso(),
            "signal_ts": s.get("ts"),
            "symbol": symbol,
            "interval": interval,
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": s.get("rr"),
            "size_btc": s.get("size_btc"),
            "risk_usd": s.get("risk_usd"),
            "outcome": outcome,
            "win": 1 if outcome == "win" else 0,
        }

        append_out(out)
        resolved[key] = {"outcome": outcome, "resolved_ts": out["resolved_ts"]}
        new += 1

    st["resolved"] = resolved
    save_state(st)

    if new:
        print(f"RESOLVED {new}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
