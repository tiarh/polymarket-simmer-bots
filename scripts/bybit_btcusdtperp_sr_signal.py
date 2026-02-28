#!/usr/bin/env python3
"""Signal-only S/R copilot for Bybit BTCUSDT USDT perpetual (5m).

- Fetches public klines from Bybit v5.
- Computes simple trend (EMA50/EMA200) and support/resistance from recent swing pivots.
- If conditions met, emits a SIGNAL payload (NO trading).
- Journals every run to JSONL + CSV.
- Optional Telegram push via `openclaw message send`.

ENV:
  BYBIT_SYMBOL=BTCUSDT (default)
  BYBIT_CATEGORY=linear (default)
  BYBIT_INTERVAL=5 (minutes)
  BYBIT_LOOKBACK=300 (candles)
  BYBIT_PIVOT_LEFT=3, BYBIT_PIVOT_RIGHT=3
  BYBIT_SR_MAX_LEVELS=8
  BYBIT_MAX_RISK_USD=3.0
  BYBIT_FIXED_SIZE_BTC=0.003
  BYBIT_RR=2 (or 3)
  BYBIT_SIGNAL_COOLDOWN_SECS=1800
  BYBIT_TELEGRAM_TARGET=<your chat id> (optional)
"""

from __future__ import annotations

import os
import json
import csv
import time
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

WORKSPACE = "/root/.openclaw/workspace"
J_JSONL = os.path.join(WORKSPACE, "memory", "bybit_sr_signal_journal.jsonl")
J_CSV = os.path.join(WORKSPACE, "memory", "bybit_sr_signal_journal.csv")
STATE = os.path.join(WORKSPACE, "memory", "bybit_sr_signal_state.json")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_json(url: str, timeout: int = 15) -> Any:
    req = Request(url, headers={"User-Agent": "bybit-sr-signal/0.1"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_signal_ts": 0}


def save_state(st: Dict[str, Any]) -> None:
    ensure_parent(STATE)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


def append_journal(row: Dict[str, Any]) -> None:
    ensure_parent(J_JSONL)
    ensure_parent(J_CSV)
    with open(J_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    file_exists = os.path.exists(J_CSV)
    with open(J_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            w.writeheader()
        w.writerow(row)


def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def rsi(values: List[float], period: int = 14) -> List[float]:
    if len(values) < period + 1:
        return [50.0] * len(values)
    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # Wilder smoothing
    avg_g = sum(gains[1:period+1]) / period
    avg_l = sum(losses[1:period+1]) / period
    out = [50.0] * (period)
    rs = avg_g / avg_l if avg_l > 0 else 999.0
    out.append(100.0 - 100.0 / (1.0 + rs))
    for i in range(period + 1, len(values)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs = avg_g / avg_l if avg_l > 0 else 999.0
        out.append(100.0 - 100.0 / (1.0 + rs))
    return out


def atr(high: List[float], low: List[float], close: List[float], period: int = 14) -> List[float]:
    if len(close) < period + 1:
        return [0.0] * len(close)
    tr = [0.0]
    for i in range(1, len(close)):
        tr_i = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
        tr.append(tr_i)
    # Wilder smoothing
    a = sum(tr[1:period+1]) / period
    out = [0.0] * period
    out.append(a)
    for i in range(period + 1, len(tr)):
        a = (a * (period - 1) + tr[i]) / period
        out.append(a)
    return out


def pivots(high: List[float], low: List[float], left: int, right: int) -> Tuple[List[int], List[int]]:
    ph, pl = [], []
    n = len(high)
    for i in range(left, n - right):
        h = high[i]
        l = low[i]
        if all(h > high[j] for j in range(i - left, i)) and all(h >= high[j] for j in range(i + 1, i + right + 1)):
            ph.append(i)
        if all(l < low[j] for j in range(i - left, i)) and all(l <= low[j] for j in range(i + 1, i + right + 1)):
            pl.append(i)
    return ph, pl


def cluster_levels(levels: List[float], max_levels: int, tol: float) -> List[float]:
    levels = sorted(levels)
    clusters: List[List[float]] = []
    for x in levels:
        placed = False
        for c in clusters:
            if abs(x - (sum(c) / len(c))) <= tol:
                c.append(x)
                placed = True
                break
        if not placed:
            clusters.append([x])
    centers = [sum(c) / len(c) for c in clusters]
    # keep most recent-ish: return closest to current price later; here just cap count
    return centers[:max_levels]


def fmt(x: float) -> str:
    return f"{x:,.2f}"


def telegram_send(text: str, media_path: Optional[str] = None) -> None:
    target = os.environ.get("BYBIT_TELEGRAM_TARGET")
    if not target:
        return
    cmd = [
        "openclaw", "message", "send",
        "--channel", "telegram",
        "--target", str(target),
        "--message", text,
    ]
    if media_path:
        cmd += ["--media", media_path]
    subprocess.run(cmd, check=False)


def main() -> int:
    sym = os.environ.get("BYBIT_SYMBOL", "BTCUSDT")
    cat = os.environ.get("BYBIT_CATEGORY", "linear")
    interval = os.environ.get("BYBIT_INTERVAL", "5")
    lookback = int(os.environ.get("BYBIT_LOOKBACK", "300"))
    left = int(os.environ.get("BYBIT_PIVOT_LEFT", "3"))
    right = int(os.environ.get("BYBIT_PIVOT_RIGHT", "3"))
    max_levels = int(os.environ.get("BYBIT_SR_MAX_LEVELS", "8"))

    max_risk = float(os.environ.get("BYBIT_MAX_RISK_USD", "3.0"))
    size_btc = float(os.environ.get("BYBIT_FIXED_SIZE_BTC", "0.003"))
    rr = float(os.environ.get("BYBIT_RR", "2"))
    cooldown = int(os.environ.get("BYBIT_SIGNAL_COOLDOWN_SECS", "1800"))

    # Bybit v5 klines
    url = (
        "https://api.bybit.com/v5/market/kline"
        f"?category={cat}&symbol={sym}&interval={interval}&limit={lookback}"
    )
    data = http_json(url, timeout=15)
    if not isinstance(data, dict) or data.get("retCode") != 0:
        row = {"ts": utc_now_iso(), "type": "error", "error": str(data)[:200]}
        append_journal(row)
        return 0

    lst = (((data.get("result") or {}).get("list")) or [])
    if not lst:
        append_journal({"ts": utc_now_iso(), "type": "skip", "reason": "no_klines"})
        return 0

    # list is reverse chronological in Bybit; sort by timestamp
    candles = sorted(lst, key=lambda r: int(r[0]))
    t = [int(r[0]) for r in candles]
    o = [float(r[1]) for r in candles]
    h = [float(r[2]) for r in candles]
    l = [float(r[3]) for r in candles]
    c = [float(r[4]) for r in candles]

    last = c[-1]
    ema50 = ema(c, 50)[-1]
    ema200 = ema(c, 200)[-1]
    trend = "UP" if ema50 > ema200 else "DOWN"

    ph, pl = pivots(h, l, left=left, right=right)
    raw_levels = [h[i] for i in ph[-20:]] + [l[i] for i in pl[-20:]]
    tol = last * 0.0015  # 15 bps clustering tolerance
    levels = cluster_levels(raw_levels, max_levels=max_levels, tol=tol)

    # pick nearest support/resistance
    supports = sorted([x for x in levels if x <= last], reverse=True)
    resist = sorted([x for x in levels if x >= last])
    sup = supports[0] if supports else None
    res = resist[0] if resist else None

    # Indicators for retest entries
    rsi14 = rsi(c, 14)[-1]
    atr14 = atr(h, l, c, 14)[-1]

    # Retest entry plan: wait for touch + rejection
    side = None
    entry = None
    sl = None
    tp = None
    reason = None
    rr_used = rr

    # Risk calc: PnL per $ move ~= size_btc
    def risk_usd(entry_px, sl_px):
        return abs(entry_px - sl_px) * size_btc

    # Define "touch" + "rejection" using last candle
    o_last, h_last, l_last, c_last = o[-1], h[-1], l[-1], c[-1]
    body = abs(c_last - o_last)
    rng = max(1e-9, h_last - l_last)
    upper_wick = h_last - max(o_last, c_last)
    lower_wick = min(o_last, c_last) - l_last

    def bullish_rejection() -> bool:
        return (lower_wick / rng) > 0.45 and (c_last > o_last)

    def bearish_rejection() -> bool:
        return (upper_wick / rng) > 0.45 and (c_last < o_last)

    atr_buf = max(tol * 0.5, atr14 * 0.25)

    if trend == "UP" and sup is not None:
        touched = l_last <= sup <= h_last
        if touched and bullish_rejection() and rsi14 >= 50:
            side = "LONG"
            entry = sup  # retest
            sl = sup - atr_buf
            # stronger if RSI higher and body decent
            strength = (rsi14 - 50) / 20.0 + min(0.5, body / rng)
            rr_used = 3.0 if strength >= 0.8 else 2.0
            if risk_usd(entry, sl) <= max_risk:
                tp = entry + rr_used * (entry - sl)
                reason = f"Retest LONG: UP trend (EMA50>EMA200), touch support {fmt(sup)} + bullish rejection, RSI={rsi14:.1f}, ATR={atr14:.1f}."
            else:
                side = None
    elif trend == "DOWN" and res is not None:
        touched = l_last <= res <= h_last
        if touched and bearish_rejection() and rsi14 <= 50:
            side = "SHORT"
            entry = res
            sl = res + atr_buf
            strength = (50 - rsi14) / 20.0 + min(0.5, body / rng)
            rr_used = 3.0 if strength >= 0.8 else 2.0
            if risk_usd(entry, sl) <= max_risk:
                tp = entry - rr_used * (sl - entry)
                reason = f"Retest SHORT: DOWN trend (EMA50<EMA200), touch resistance {fmt(res)} + bearish rejection, RSI={rsi14:.1f}, ATR={atr14:.1f}."
            else:
                side = None

    row: Dict[str, Any] = {
        "ts": utc_now_iso(),
        "type": "analysis",
        "symbol": sym,
        "interval": interval,
        "price": last,
        "ema50": ema50,
        "ema200": ema200,
        "trend": trend,
        "rsi14": rsi14,
        "atr14": atr14,
        "support": sup,
        "resistance": res,
        "candle_ts": t[-1],
    }

    if side and entry and sl and tp:
        st = load_state()
        now_ts = time.time()
        if cooldown and (now_ts - float(st.get("last_signal_ts") or 0)) < cooldown:
            row.update({"type": "skip", "reason": "signal_cooldown"})
            append_journal(row)
            return 0

        r_usd = risk_usd(entry, sl)
        row.update({
            "type": "signal",
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr_used,
            "size_btc": size_btc,
            "risk_usd": r_usd,
            "reason": reason,
        })
        append_journal(row)
        st["last_signal_ts"] = now_ts
        save_state(st)

        tv_symbol = os.environ.get("TV_SYMBOL", "BYBIT:BTCUSDT.P")
        tv_link = f"https://www.tradingview.com/chart/?symbol={tv_symbol}&interval={interval}"
        row["tv_symbol"] = tv_symbol
        row["tv_link"] = tv_link

        # TradingView snapshot + RR overlay
        snap = os.path.join(WORKSPACE, "memory", "bybit_tv_last.png")
        snap_rr = os.path.join(WORKSPACE, "memory", "bybit_tv_last_rr.png")
        media = None
        try:
            subprocess.run([
                "/root/weather-env/bin/python",
                os.path.join(WORKSPACE, "scripts", "tv_snapshot.py"),
                "--url", tv_link,
                "--out", snap,
            ], timeout=60, check=False)

            if os.path.exists(snap):
                # Use recent candle range as plot range estimate
                pmin = min(l[-60:])
                pmax = max(h[-60:])
                subprocess.run([
                    "/root/weather-env/bin/python",
                    os.path.join(WORKSPACE, "scripts", "rr_overlay.py"),
                    "--in", snap,
                    "--out", snap_rr,
                    "--entry", str(entry),
                    "--sl", str(sl),
                    "--tp", str(tp),
                    "--pmin", str(pmin),
                    "--pmax", str(pmax),
                    "--side", side,
                ], timeout=30, check=False)
                if os.path.exists(snap_rr):
                    media = snap_rr
                else:
                    media = snap
        except Exception:
            media = None

        # Mark-to-market estimate (if the limit fills at entry)
        if side == "LONG":
            mtm = (last - entry) * size_btc
        else:
            mtm = (entry - last) * size_btc
        row["mtm_if_filled_usd"] = mtm

        msg = (
            f"BYBIT SIGNAL (trial, no trade)\n"
            f"{sym} {interval}m | Trend: {trend}\n"
            f"Side: {side}\n"
            f"Entry (limit): {fmt(entry)}\n"
            f"SL: {fmt(sl)} (risk≈${r_usd:.2f})\n"
            f"TP: {fmt(tp)} (R:R 1:{int(rr_used)})\n"
            f"Size: {size_btc:.4f} BTC\n"
            f"Now: {fmt(last)} | MTM (if filled): ${mtm:+.2f}\n"
            f"S/R: support={fmt(sup)} | resistance={fmt(res)}\n"
            f"TV: {tv_link}\n"
            f"Reason: {reason}"
        )
        telegram_send(msg, media_path=media)
        return 0

    row.update({"type": "skip", "reason": "no_setup_or_risk_too_high", "max_risk_usd": max_risk, "size_btc": size_btc})
    append_journal(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
