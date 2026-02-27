#!/usr/bin/env python3
"""Resolve PAPER intents for BTC 15m arb journal and compute win-rate/PnL.

Reads:   memory/btc_15m_arb_journal.jsonl
Writes:  memory/btc_15m_arb_results.jsonl (+ csv)
State:   memory/btc_15m_arb_resolve_state.json

Resolution source: Simmer SDK market endpoint (/api/sdk/markets/{id}) which includes
status/outcome for Polymarket-imported fast markets.

This is PAPER accounting (not actual fills). It assumes fill at journal 'price' for 'shares'.
"""

from __future__ import annotations

import os
import json
import csv
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

sys.stdout.reconfigure(line_buffering=True)

SIMMER_API_BASE = "https://api.simmer.markets"

WORKSPACE = "/root/.openclaw/workspace"
JOURNAL = os.path.join(WORKSPACE, "memory", "btc_15m_arb_journal.jsonl")
OUT_JSONL = os.path.join(WORKSPACE, "memory", "btc_15m_arb_results.jsonl")
OUT_CSV = os.path.join(WORKSPACE, "memory", "btc_15m_arb_results.csv")
STATE_PATH = os.path.join(WORKSPACE, "memory", "btc_15m_arb_resolve_state.json")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_request(api_key: str, endpoint: str) -> Any:
    url = f"{SIMMER_API_BASE}{endpoint}"
    req = Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "btc15m-arb-resolver/0.1",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise RuntimeError(f"API error {e.code}: {body}")
    except URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}")


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"resolved": {}}


def save_state(state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def append_result(row: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(OUT_JSONL), exist_ok=True)
    with open(OUT_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    file_exists = os.path.exists(OUT_CSV)
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            w.writeheader()
        w.writerow(row)


def paper_pnl(side: str, price: float, shares: float, outcome_up: bool, fee_rate_bps: float = 0.0) -> Dict[str, float]:
    """Binary contract priced in [0,1], pays 1 if YES outcome true.

    For NO, we treat it as YES on DOWN with price = 1-yes.

    fee approximation: fee_rate_bps applied to notional (shares * price).
    """
    notional = shares * price
    fee = notional * (fee_rate_bps / 10000.0)

    win = outcome_up if side == "YES" else (not outcome_up)
    if win:
        gross = shares * (1.0 - price)
    else:
        gross = -shares * price

    net = gross - fee
    return {
        "notional": float(notional),
        "fee": float(fee),
        "pnl_gross": float(gross),
        "pnl_net": float(net),
        "win": 1.0 if win else 0.0,
    }


def main() -> int:
    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        print("SIMMER_API_KEY not set")
        return 2

    state = load_state()
    resolved = state.get("resolved", {})

    if not os.path.exists(JOURNAL):
        print("no_journal")
        return 0

    new_resolved = 0
    with open(JOURNAL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue

            if r.get("action") not in ("PAPER_INTENT", "TRADE_INTENT"):
                continue

            market_id = r.get("market_id")
            if not market_id:
                continue

            # unique key = market_id + ts (multiple intents possible per market)
            key = f"{market_id}:{r.get('ts')}"
            if key in resolved:
                continue

            # Fetch market status/outcome
            try:
                mres = api_request(api_key, f"/api/sdk/markets/{market_id}")
                mk = mres.get("market", {}) if isinstance(mres, dict) else {}
            except Exception:
                continue

            status = (mk.get("status") or "").lower()
            if status not in ("resolved", "closed", "settled"):
                # still active
                continue

            outcome = mk.get("outcome")
            # outcome may be bool-like or string; for Up/Down markets assume outcome_name hints
            outcome_name = (mk.get("outcome_name") or "").lower()
            outcome_up: Optional[bool] = None
            if isinstance(outcome, bool):
                outcome_up = outcome
            elif isinstance(outcome, (int, float)):
                outcome_up = bool(outcome)
            elif isinstance(outcome, str):
                o = outcome.strip().lower()
                if o in ("yes", "up", "true", "1"):
                    outcome_up = True
                if o in ("no", "down", "false", "0"):
                    outcome_up = False

            if outcome_up is None:
                if "up" in outcome_name:
                    outcome_up = True
                elif "down" in outcome_name:
                    outcome_up = False

            if outcome_up is None:
                # can't resolve yet
                continue

            side = r.get("side")
            price = float(r.get("price") or 0)
            shares = float(r.get("shares") or 0)
            fee_rate_bps = float(mk.get("fee_rate_bps") or 0)

            # --- Paper fill model (deterministic) ---
            # Not all intents would fill at the logged price. Use a simple fill probability based on edge/conf.
            edge = float(r.get("edge") or 0.0)
            conf = float(r.get("conf") or 0.0)

            # map to [0, 0.6]
            p_edge = max(0.0, min(1.0, (edge - 0.02) / 0.06))
            p_conf = max(0.0, min(1.0, (conf - 0.60) / 0.20))
            p_fill = 0.05 + 0.55 * (0.6 * p_edge + 0.4 * p_conf)
            p_fill = max(0.02, min(0.60, p_fill))

            # deterministic coin flip based on key hash
            h = abs(hash(key)) % 10_000
            u = h / 10_000.0
            filled = u < p_fill

            if not filled:
                out = {
                    "resolved_ts": utc_now_iso(),
                    "intent_ts": r.get("ts"),
                    "market_id": market_id,
                    "question": mk.get("question") or r.get("question"),
                    "side": side,
                    "entry_price": price,
                    "shares": shares,
                    "status": status,
                    "outcome_up": outcome_up,
                    "outcome_name": mk.get("outcome_name"),
                    "fee_rate_bps": fee_rate_bps,
                    "filled": False,
                    "p_fill": p_fill,
                    "u": u,
                    "notional": 0.0,
                    "fee": 0.0,
                    "pnl_gross": 0.0,
                    "pnl_net": 0.0,
                    "win": 0.0,
                }
                append_result(out)
                resolved[key] = {"resolved_ts": out["resolved_ts"], "pnl_net": out["pnl_net"], "win": out["win"], "filled": False}
                new_resolved += 1
                continue

            calc = paper_pnl(side=side, price=price, shares=shares, outcome_up=outcome_up, fee_rate_bps=fee_rate_bps)

            out = {
                "resolved_ts": utc_now_iso(),
                "intent_ts": r.get("ts"),
                "market_id": market_id,
                "question": mk.get("question") or r.get("question"),
                "side": side,
                "entry_price": price,
                "shares": shares,
                "status": status,
                "outcome_up": outcome_up,
                "outcome_name": mk.get("outcome_name"),
                "fee_rate_bps": fee_rate_bps,
                "filled": True,
                "p_fill": p_fill,
                "u": u,
                **calc,
            }

            append_result(out)
            resolved[key] = {"resolved_ts": out["resolved_ts"], "pnl_net": out["pnl_net"], "win": out["win"]}
            new_resolved += 1

    state["resolved"] = resolved
    save_state(state)

    if new_resolved:
        print(f"RESOLVED {new_resolved}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
