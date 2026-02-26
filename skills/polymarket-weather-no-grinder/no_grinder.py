#!/usr/bin/env python3
"""Polymarket Weather NO Grinder

High win-rate style: buy NO on narrow temperature buckets when YES is overpriced.
Uses Simmer SDK for market list + context + trading.

Dry-run by default; pass --live to execute.
"""

import os
import re
import json
import csv
import sys
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

TRADE_SOURCE = "sdk:weather-no-grinder"

API_KEY = os.environ.get("SIMMER_API_KEY")

# Config
LOCATIONS = [s.strip().upper() for s in os.environ.get(
    "SIMMER_NO_LOCATIONS",
    "NYC,CHICAGO,SEATTLE,ATLANTA,DALLAS,MIAMI,BUENOS AIRES,SAO PAULO,LONDON,PARIS,ANKARA,WELLINGTON",
).split(",") if s.strip()]

ENTRY_YES_GTE = float(os.environ.get("SIMMER_NO_ENTRY_YES_GTE", "0.93"))
# Avoid paying near-$1.00 for NO (terrible convexity; can still lose almost entire stake)
MIN_YES_PRICE = float(os.environ.get("SIMMER_NO_MIN_YES_PRICE", "0.05"))
MAX_NO_PRICE = float(os.environ.get("SIMMER_NO_MAX_NO_PRICE", "0.98"))

MAX_POSITION = float(os.environ.get("SIMMER_NO_MAX_POSITION", "3.00"))
MAX_TRADES = int(os.environ.get("SIMMER_NO_MAX_TRADES", "3"))
MAX_SPREAD_PCT = float(os.environ.get("SIMMER_NO_MAX_SPREAD_PCT", "0.10"))
MIN_HOURS_TO_RESOLVE = float(os.environ.get("SIMMER_NO_MIN_HOURS_TO_RESOLVE", "6"))
COOLDOWN_MINUTES = int(os.environ.get("SIMMER_NO_COOLDOWN_MINUTES", "120"))
MAX_ENTRIES_DEFAULT = int(os.environ.get("SIMMER_NO_MAX_ENTRIES_DEFAULT", "3"))
MAX_ENTRIES_HIGH = int(os.environ.get("SIMMER_NO_MAX_ENTRIES_HIGH", "5"))
HIGH_CONVICTION_YES = float(os.environ.get("SIMMER_NO_HIGH_CONVICTION_YES", "0.97"))

# Exits (based on YES price moving down)
TP1_YES_LTE = float(os.environ.get("SIMMER_NO_TP1_YES_LTE", "0.85"))
TP2_YES_LTE = float(os.environ.get("SIMMER_NO_TP2_YES_LTE", "0.81"))

# Safety time-exit when resolving soon AND position is losing
SAFETY_EXIT_HOURS = float(os.environ.get("SIMMER_NO_SAFETY_EXIT_HOURS", "1"))

# Optional force-exit: if YES stays extremely high (NO extremely expensive / bad convexity), cut.
# Disabled by default (set 0 to disable).
FORCE_EXIT_YES_GTE = float(os.environ.get("SIMMER_NO_FORCE_EXIT_YES_GTE", "0"))

JOURNAL_JSONL = Path(os.environ.get(
    "SIMMER_NO_JOURNAL_JSONL",
    "/root/.openclaw/workspace/memory/no_grinder_journal.jsonl",
))
JOURNAL_CSV = Path(os.environ.get(
    "SIMMER_NO_JOURNAL_CSV",
    "/root/.openclaw/workspace/memory/no_grinder_journal.csv",
))
STATE_DIR = Path(__file__).parent / "state"
COOLDOWN_FILE = STATE_DIR / "cooldown.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_journal(event: dict):
    try:
        event = dict(event)
        event.setdefault("ts", now_iso())
        JOURNAL_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        cols = [
            "ts","type","market_id","question","outcome_name","yes_price","spread_pct",
            "hours_to_resolve","amount","shares","trade_id","simulated","error",
        ]
        JOURNAL_CSV.parent.mkdir(parents=True, exist_ok=True)
        write_header = not JOURNAL_CSV.exists()
        with open(JOURNAL_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            if write_header:
                w.writeheader()
            w.writerow({c: event.get(c) for c in cols})
    except Exception:
        return


def load_cooldown():
    try:
        if not COOLDOWN_FILE.exists():
            return {}
        return json.loads(COOLDOWN_FILE.read_text()) or {}
    except Exception:
        return {}


def save_cooldown(state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COOLDOWN_FILE.write_text(json.dumps(state, indent=2))


def cooldown_allows(market_id: str) -> tuple[bool, int]:
    if COOLDOWN_MINUTES <= 0:
        return True, 0
    st = load_cooldown()
    ts = st.get(str(market_id))
    if not ts:
        return True, 0
    try:
        last = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except Exception:
        return True, 0
    remaining = (last + timedelta(minutes=COOLDOWN_MINUTES)) - datetime.now(timezone.utc)
    mins = int(max(0, remaining.total_seconds() // 60))
    return remaining.total_seconds() <= 0, mins


def mark_traded(market_id: str):
    st = load_cooldown()
    st[str(market_id)] = datetime.now(timezone.utc).timestamp()
    save_cooldown(st)


def load_entry_counts():
    try:
        p = STATE_DIR / "entry_counts.json"
        if not p.exists():
            return {}
        return json.loads(p.read_text()) or {}
    except Exception:
        return {}


def save_entry_counts(counts: dict):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "entry_counts.json").write_text(json.dumps(counts, indent=2))
    except Exception:
        return


def get_client(live: bool):
    try:
        from simmer_sdk import SimmerClient
    except ImportError:
        print("Error: simmer-sdk not installed in this python env")
        sys.exit(1)
    if not API_KEY:
        print("Error: SIMMER_API_KEY not set")
        sys.exit(1)
    return SimmerClient(api_key=API_KEY, venue=os.environ.get("TRADING_VENUE", "polymarket"), live=live)


def get_positions(client):
    try:
        return client._request("GET", "/api/sdk/positions").get("positions", [])
    except Exception:
        return []


def market_hours_to_resolve(resolves_at: str):
    if not resolves_at:
        return None
    try:
        dt = datetime.fromisoformat(resolves_at.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
    except Exception:
        return None


def sell_no(client, market_id: str, shares: float):
    # Simmer SDK supports action="sell" + side="no".
    return client.trade(market_id=market_id, side="no", action="sell", shares=float(shares), source=TRADE_SOURCE)


def parse_location(title: str) -> str | None:
    # crude: "in Miami" / "in New York City" / etc.
    m = re.search(r"in ([A-Za-z ]+?) (be|on)", title)
    if not m:
        return None
    loc = m.group(1).strip().upper()
    # normalize a bit
    if loc in ("NEW YORK CITY", "NEW YORK"):
        return "NYC"
    return loc


def is_narrow_bucket(text: str) -> bool:
    t = (text or "").lower()
    if "between" in t:
        # between 34-35°F etc -> narrow
        return True
    # exact like "be 23°C on" or "be 11°C"
    if re.search(r"be \d+\s*°[cf]", t):
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--exit-only", action="store_true", help="Only manage exits; never open new entries")
    args = ap.parse_args()

    dry_run = not args.live
    client = get_client(live=not dry_run)

    print("🧊 Weather NO Grinder")
    print(f"  Mode: {'LIVE' if args.live else 'PAPER'}")
    print(f"  Locations: {', '.join(LOCATIONS)}")
    print(f"  Entry: buy NO when YES >= {ENTRY_YES_GTE:.2f}")
    print(f"  Guardrail: skip if YES < {MIN_YES_PRICE:.2f} or NO > {MAX_NO_PRICE:.2f}")
    print(f"  TP1: sell ~50% when YES <= {TP1_YES_LTE:.2f} | TP2: sell rest when YES <= {TP2_YES_LTE:.2f}")
    print(f"  Safety: exit if <= {SAFETY_EXIT_HOURS:.1f}h to resolve AND losing")
    print(f"  Max position: ${MAX_POSITION:.2f} | max trades/run: {MAX_TRADES}")
    print(f"  Spread max: {MAX_SPREAD_PCT*100:.1f}% | min hours to resolve: {MIN_HOURS_TO_RESOLVE}")

    # 1) Exit management for existing NO-grinder positions
    positions = get_positions(client)
    exits = 0
    for p in positions:
        sources = p.get("sources", []) or []
        if TRADE_SOURCE not in sources:
            continue
        market_id = p.get("market_id")
        shares_no = float(p.get("shares_no") or 0)
        if shares_no < 5:
            continue

        # Determine current YES price (positions API uses current_price as YES)
        yes_price = p.get("current_price") or p.get("price_yes")
        if yes_price is None:
            continue
        yes_price = float(yes_price)

        pnl = p.get("pnl")
        is_losing = isinstance(pnl, (int, float)) and float(pnl) < 0

        # Need context for resolves_at
        try:
            ctx = client._request("GET", f"/api/sdk/context/{market_id}")
            mk = (ctx.get("market") or {})
            hours_to_resolve = market_hours_to_resolve(mk.get("resolves_at"))
        except Exception:
            hours_to_resolve = None

        sell_target = 0.0
        reason = None

        # Force-exit if YES is extremely high (user-defined). This is independent of PnL.
        if FORCE_EXIT_YES_GTE and yes_price >= float(FORCE_EXIT_YES_GTE):
            sell_target = shares_no
            reason = f"force_exit (YES>= {FORCE_EXIT_YES_GTE})"
        # Safety time-exit (only if losing)
        elif SAFETY_EXIT_HOURS and hours_to_resolve is not None and hours_to_resolve <= float(SAFETY_EXIT_HOURS) and is_losing:
            sell_target = shares_no
            reason = f"safety_exit (<= {SAFETY_EXIT_HOURS}h & losing)"
        else:
            # Tiered take-profit based on YES price falling
            if yes_price <= float(TP2_YES_LTE):
                sell_target = shares_no
                reason = f"tp2 (YES<= {TP2_YES_LTE})"
            elif yes_price <= float(TP1_YES_LTE):
                sell_target = max(5.0, shares_no / 2.0)
                # don't leave dust
                if (shares_no - sell_target) < 5.0:
                    sell_target = shares_no
                reason = f"tp1 (YES<= {TP1_YES_LTE})"

        if sell_target > 0:
            tag = "SIMULATED" if dry_run else "LIVE"
            print(f"\nExit candidate: YES {yes_price:.3f} | {reason} | selling {sell_target:.1f}/{shares_no:.1f} shares ({tag})")
            try:
                result = sell_no(client, market_id, sell_target)
                ok = bool(getattr(result, 'success', False))
                trade_id = getattr(result, 'trade_id', None)
                simulated = bool(getattr(result, 'simulated', False))
                err = getattr(result, 'error', None)
            except Exception as e:
                ok = False
                trade_id = None
                simulated = dry_run
                err = str(e)

            append_journal({
                "type": "sell_no",
                "market_id": market_id,
                "question": p.get("question") or "(unknown)",
                "yes_price": yes_price,
                "hours_to_resolve": hours_to_resolve,
                "amount": None,
                "shares": float(sell_target),
                "trade_id": trade_id,
                "simulated": simulated,
                "error": err,
            })

            if ok:
                exits += 1
                mark_traded(market_id)
                print(f"  ✅ {'[PAPER] ' if simulated else ''}Sold NO shares: {sell_target:.1f}")
            else:
                print(f"  ❌ Sell failed: {err}")

            if exits >= MAX_TRADES:
                break

    # 2) Entry scanning (skipped in exit-only mode)
    if args.exit_only:
        print("\nExit-only mode enabled: skipping entry scan.")
        print("\nDone. Trades: 0")
        return

    try:
        res = client._request("GET", "/api/sdk/markets", params={"tags":"weather","status":"active","limit":args.limit})
        markets = res.get("markets", [])
    except Exception as e:
        print(f"Fetch markets failed: {e}")
        return

    trades = 0
    for m in markets:
        if trades >= MAX_TRADES:
            break
        q = m.get("question") or ""
        if "highest temperature" not in q.lower():
            continue
        if not is_narrow_bucket(q):
            continue
        loc = parse_location(q)
        if loc and loc not in LOCATIONS:
            continue

        market_id = m.get("id")
        yes_price = m.get("external_price_yes")
        if yes_price is None:
            continue
        if float(yes_price) < ENTRY_YES_GTE:
            continue

        # Guardrail: don't buy NO when NO is extremely expensive (i.e., YES extremely cheap)
        if float(yes_price) < float(MIN_YES_PRICE):
            continue
        no_price = 1.0 - float(yes_price)
        if no_price > float(MAX_NO_PRICE):
            continue

        # Enforce cooldown
        allowed, mins = cooldown_allows(market_id)
        if not allowed:
            continue

        # Enforce max averaging entries per market (conditional)
        counts = load_entry_counts()
        used = int(counts.get(str(market_id), 0))

        # high conviction heuristic: YES extremely high (we're buying NO), i.e. NO very cheap
        # This is a blunt proxy; keep risk caps small.
        cap = int(MAX_ENTRIES_DEFAULT)
        try:
            if float(yes_price) >= float(HIGH_CONVICTION_YES):
                cap = int(MAX_ENTRIES_HIGH)
        except Exception:
            pass

        if used >= cap:
            continue

        # Context checks
        try:
            ctx = client._request("GET", f"/api/sdk/context/{market_id}")
        except Exception:
            continue

        sl = (ctx.get("slippage") or {})
        spread = sl.get("spread_pct")
        if spread is not None and float(spread) > float(MAX_SPREAD_PCT):
            continue

        mk = (ctx.get("market") or {})
        resolves_at = mk.get("resolves_at")
        hours_to_resolve = None
        if resolves_at:
            try:
                dt = datetime.fromisoformat(resolves_at.replace("Z", "+00:00"))
                hours_to_resolve = (dt - datetime.now(timezone.utc)).total_seconds()/3600.0
            except Exception:
                pass
        if hours_to_resolve is not None and hours_to_resolve < MIN_HOURS_TO_RESOLVE:
            continue

        outcome_name = m.get("outcome_name") or ""

        print(f"\nCandidate: YES {yes_price:.3f} | spread {float(spread)*100 if spread is not None else None:.1f}% | {q[:90]}")

        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        def _pick_first(obj, keys, default=None):
            for k in keys:
                v = _get(obj, k)
                if v is None:
                    continue
                try:
                    if isinstance(v, (int, float)):
                        return float(v)
                    # numeric-like strings
                    return float(str(v))
                except Exception:
                    continue
            return default

        # Execute buy NO
        try:
            result = client.trade(market_id=market_id, side="no", amount=float(MAX_POSITION), source=TRADE_SOURCE)
            ok = bool(_get(result, "success", False))
            trade_id = _get(result, "trade_id", None)
            simulated = bool(_get(result, "simulated", False))
            err = _get(result, "error", None)

            # Simmer SDK result field name varies; try a bunch.
            shares = _pick_first(result, [
                "shares_bought",
                "shares",
                "shares_filled",
                "filled_shares",
                "shares_acquired",
                "size",
            ], default=0.0) or 0.0

            # If API doesn't return shares, approximate from amount / entry price.
            if shares <= 0:
                approx_price = max(1e-9, no_price)
                shares = float(MAX_POSITION) / approx_price
        except Exception as e:
            ok = False
            trade_id = None
            shares = float(MAX_POSITION) / max(1e-9, no_price)
            simulated = dry_run
            err = str(e)

        append_journal({
            "type": "buy_no",
            "market_id": market_id,
            "question": q,
            "outcome_name": outcome_name,
            "yes_price": float(yes_price),
            "spread_pct": float(spread) if spread is not None else None,
            "hours_to_resolve": hours_to_resolve,
            "amount": float(MAX_POSITION),
            "shares": shares,
            "trade_id": trade_id,
            "simulated": simulated,
            "error": err,
        })

        if ok:
            print(f"  ✅ {'[PAPER] ' if simulated else ''}Bought NO shares: {shares:.1f} (amount ${MAX_POSITION:.2f})")
            mark_traded(market_id)
            counts = load_entry_counts()
            counts[str(market_id)] = int(counts.get(str(market_id), 0)) + 1
            save_entry_counts(counts)
            trades += 1
        else:
            print(f"  ❌ Trade failed: {err}")

    print(f"\nDone. Trades: {trades}")


if __name__ == "__main__":
    main()
