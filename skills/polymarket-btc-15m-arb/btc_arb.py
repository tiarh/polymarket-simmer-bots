#!/usr/bin/env python3
"""15m BTC Polymarket arb (paper-first).

This is a minimal, robust loop that:
- discovers BTC up/down 15m markets (Gamma) indirectly via Simmer discovery endpoint if available,
  otherwise relies on configured market_ids.
- fetches Simmer context + best bid/ask.
- compares to a fair price derived from CEX move probability proxy.

NOTE: The current version is PAPER by default; LIVE requires SIMMER_BTC_ARB_LIVE=1.
"""

from __future__ import annotations

import os
import sys
import time
import json
import csv
import math
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

sys.stdout.reconfigure(line_buffering=True)

SIMMER_API_BASE = "https://api.simmer.markets"

JOURNAL_JSONL = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "memory", "btc_15m_arb_journal.jsonl")
JOURNAL_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "memory", "btc_15m_arb_journal.csv")
STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "memory", "btc_15m_arb_state.json")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_request(api_key: str, endpoint: str) -> Any:
    url = f"{SIMMER_API_BASE}{endpoint}"
    req = Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise RuntimeError(f"API error {e.code}: {body}")
    except URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}")


def ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_intent_ts": {}}


def save_state(st: Dict[str, Any]) -> None:
    ensure_parent(STATE_PATH)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


def append_journal(row: Dict[str, Any]) -> None:
    ensure_parent(JOURNAL_JSONL)
    ensure_parent(JOURNAL_CSV)

    with open(JOURNAL_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # CSV header management
    file_exists = os.path.exists(JOURNAL_CSV)
    with open(JOURNAL_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            w.writeheader()
        w.writerow(row)


@dataclass
class MarketQuote:
    market_id: str
    question: str
    best_yes: Optional[float]
    best_no: Optional[float]
    spread: Optional[float]


def get_positions_summary(api_key: str) -> Dict[str, Any]:
    # Used for risk gates (concurrent, exposure, pnl_24h if present)
    return api_request(api_key, "/api/sdk/portfolio")


def get_open_positions(api_key: str) -> List[Dict[str, Any]]:
    res = api_request(api_key, "/api/sdk/positions")
    if isinstance(res, dict) and "positions" in res:
        return res["positions"]
    if isinstance(res, list):
        return res
    return []


def fetch_context(api_key: str, market_id: str) -> Dict[str, Any]:
    return api_request(api_key, f"/api/sdk/context/{market_id}")


def discover_gamma_markets(slug_prefix: str, limit: int = 50) -> List[Dict[str, Any]]:
    url = (
        "https://gamma-api.polymarket.com/markets"
        "?limit=50&closed=false&tag=crypto&order=createdAt&ascending=false"
    )
    data = _http_json(url, timeout=15)
    if not isinstance(data, list):
        return []
    out = []
    for m in data:
        slug = (m.get("slug") or "")
        if not slug.startswith(slug_prefix):
            continue
        out.append(m)
        if len(out) >= limit:
            break
    return out


def discover_simmer_btc15m_market_ids(api_key: str, limit: int = 12) -> List[str]:
    """Discover Simmer market_ids for BTC fast-15m markets.

    This uses Simmer's own index (import_source=polymarket, tags include fast-15m),
    which is what we actually need to trade via SDK.
    """
    res = api_request(api_key, "/api/sdk/markets?tags=fast-15m&limit=200")
    mkts = res.get("markets", []) if isinstance(res, dict) else []

    rows = []
    for m in mkts:
        q = (m.get("question") or "")
        if "Bitcoin Up or Down" not in q:
            continue
        ra = m.get("resolves_at")
        rows.append((ra, m.get("id"), q))

    # Sort by resolves_at string (ISO) which is sortable lexicographically
    rows.sort(key=lambda x: x[0] or "")

    out: List[str] = []
    for _ra, mid, _q in rows:
        if mid:
            out.append(mid)
        if len(out) >= limit:
            break
    return out


def discover_simmer_market_ids_from_gamma(api_key: str, slug_prefix: str, limit: int = 12) -> List[str]:
    """(Optional) Use Gamma as a hint, but fall back to Simmer discovery.

    Gamma can be ahead of Simmer imports for the newest epochs.
    So this function tries to map Gamma questions to Simmer ids; if mapping is empty,
    we fall back to Simmer's own fast-15m list.
    """
    try:
        gamma = discover_gamma_markets(slug_prefix=slug_prefix, limit=limit)
    except Exception:
        gamma = []

    # Lookup question->id from Simmer
    try:
        res = api_request(api_key, "/api/sdk/markets?tags=fast-15m&limit=200")
        simmer_markets = res.get("markets", []) if isinstance(res, dict) else []
    except Exception:
        simmer_markets = []

    def norm(s: str) -> str:
        s = (s or "").strip().lower()
        s = s.replace("\u2013", "-").replace("\u2014", "-")
        s = " ".join(s.split())
        return s

    by_q: Dict[str, str] = {norm(m.get("question")): m.get("id") for m in simmer_markets if m.get("question") and m.get("id")}

    ids: List[str] = []
    for gm in gamma:
        mid = by_q.get(norm(gm.get("question") or ""))
        if mid:
            ids.append(mid)

    # if Gamma mapping fails (often for the newest markets), fall back
    if not ids:
        return discover_simmer_btc15m_market_ids(api_key, limit=limit)

    # De-dup preserve order
    seen = set()
    out = []
    for mid in ids:
        if mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out[:limit]


def fetch_quote(api_key: str, market_id: str) -> MarketQuote:
    ctx = fetch_context(api_key, market_id)
    question = ctx.get("question") or market_id

    # Simmer returns nested market data; for binary markets, current_probability is a good proxy for YES price.
    mk = ctx.get("market") if isinstance(ctx, dict) else None
    if isinstance(mk, dict):
        yes = mk.get("current_probability")
        # Some endpoints also expose current_price; treat it as probability for binary markets.
        if yes is None:
            yes = mk.get("current_price")
        no = (1.0 - float(yes)) if yes is not None else None
    else:
        # Fallbacks (older schemas)
        yes = ctx.get("best_yes") or ctx.get("yes_best_ask") or ctx.get("yes_price") or ctx.get("current_probability")
        no = ctx.get("best_no") or ctx.get("no_best_ask") or ctx.get("no_price")

    best_yes = float(yes) if yes is not None else None
    best_no = float(no) if no is not None else None

    spread = None
    if best_yes is not None and best_no is not None:
        spread = abs((best_yes + best_no) - 1.0)

    return MarketQuote(market_id=market_id, question=question, best_yes=best_yes, best_no=best_no, spread=spread)


def _http_json(url: str, timeout: int = 15, headers: Optional[Dict[str, str]] = None) -> Any:
    req_headers = headers or {}
    if "User-Agent" not in req_headers:
        req_headers["User-Agent"] = "btc15m-arb/0.1"
    req = Request(url, headers=req_headers)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fair_prob_from_move_proxy() -> Tuple[float, float]:
    """Estimate fair probability of UP over next 15m from Binance.

    This is intentionally simple and paper-safe:
    - Pull last 2 x 15m klines for BTCUSDT.
    - Use last-candle return as a drift proxy.
    - Map drift to probability via logistic with tunable scale.

    Returns: (fair_up_prob, confidence)
    """
    sym = os.environ.get("SIMMER_BTC_ARB_SYMBOL", "BTCUSDT")
    interval = os.environ.get("SIMMER_BTC_ARB_KLINE_INTERVAL", "15m")
    scale = float(os.environ.get("SIMMER_BTC_ARB_DRIFT_SCALE", "250"))  # bigger => smaller moves

    url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={interval}&limit=2"
    try:
        kl = _http_json(url, timeout=10)
        if not isinstance(kl, list) or len(kl) < 2:
            return 0.50, 0.50
        # kline format: [openTime, open, high, low, close, volume, ...]
        o = float(kl[-1][1])
        c = float(kl[-1][4])
        r = (c - o) / o if o > 0 else 0.0

        # logistic mapping
        x = clamp(r * scale, -6.0, 6.0)
        fair = 1.0 / (1.0 + math.exp(-x))

        # confidence increases with |r| but capped
        conf = clamp(0.55 + min(abs(r) * scale / 10.0, 0.35), 0.50, 0.90)
        return float(fair), float(conf)
    except Exception:
        return 0.50, 0.50


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_edge(side: str, mkt_prob: float, fair_prob: float) -> float:
    # Edge as relative advantage in probability terms
    if side == "YES":
        return fair_prob - mkt_prob
    return (1 - fair_prob) - (1 - mkt_prob)


def shares_for_usd(usd: float, price: float) -> float:
    if price <= 0:
        return 0.0
    return usd / price


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-id", action="append", default=[], help="Explicit market_id(s) to trade")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args()

    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        print("SIMMER_API_KEY not set")
        return 2

    live = os.environ.get("SIMMER_BTC_ARB_LIVE", "0") == "1"

    max_pos = float(os.environ.get("SIMMER_BTC_ARB_MAX_POSITION_USD", "200"))
    max_conc = int(os.environ.get("SIMMER_BTC_ARB_MAX_CONCURRENT", "2"))
    daily_loss = float(os.environ.get("SIMMER_BTC_ARB_DAILY_LOSS_LIMIT_USD", "50"))
    edge_min = float(os.environ.get("SIMMER_BTC_ARB_EDGE_MIN", "0.02"))
    conf_min = float(os.environ.get("SIMMER_BTC_ARB_CONF_MIN", "0.60"))
    liq_min = float(os.environ.get("SIMMER_BTC_ARB_LIQUIDITY_MIN_USD", "1000"))

    min_shares = float(os.environ.get("SIMMER_MIN_SHARES", "5"))
    max_bump = float(os.environ.get("SIMMER_BTC_ARB_MAX_BUMP_USD", "5"))

    market_ids = args.market_id

    # Discovery via Gamma → Simmer mapping (paper-first)
    discovery = os.environ.get("SIMMER_BTC_ARB_DISCOVERY", "1") == "1"
    max_markets = int(os.environ.get("SIMMER_BTC_ARB_MAX_MARKETS", "12"))
    slug_prefix = os.environ.get("SIMMER_BTC_ARB_MARKET_SLUG_PREFIX", "btc-updown-15m-")

    if not market_ids and discovery:
        market_ids = discover_simmer_market_ids_from_gamma(api_key, slug_prefix=slug_prefix, limit=max_markets)

    if not market_ids:
        skip = {
            "ts": utc_now_iso(),
            "source": "btc15m-arb",
            "action": "SKIP",
            "reason": "no_market_ids_configured",
            "live": live,
        }
        append_journal(skip)
        if not args.quiet:
            print("SKIP: no market_ids configured; enable discovery or pass --market-id ...")
        return 0

    # Risk gates from portfolio
    # NOTE: for PAPER mode, do NOT block on account-wide open positions; otherwise paper never runs when other bots have positions.
    portfolio = get_positions_summary(api_key)
    pnl_24h = portfolio.get("pnl_24h")
    positions_count = int(portfolio.get("positions_count", 0) or 0)

    if live:
        if pnl_24h is not None and float(pnl_24h) <= -abs(daily_loss):
            row = {
                "ts": utc_now_iso(),
                "source": "btc15m-arb",
                "action": "SKIP",
                "reason": "daily_loss_limit",
                "pnl_24h": pnl_24h,
                "live": live,
            }
            append_journal(row)
            if not args.quiet:
                print("SKIP: daily loss limit")
            return 0

        if positions_count >= max_conc:
            row = {
                "ts": utc_now_iso(),
                "source": "btc15m-arb",
                "action": "SKIP",
                "reason": "max_concurrent",
                "positions_count": positions_count,
                "live": live,
            }
            append_journal(row)
            if not args.quiet:
                print("SKIP: max concurrent")
            return 0

    fair_prob, confidence = fair_prob_from_move_proxy()

    for mid in market_ids:
        try:
            q = fetch_quote(api_key, mid)
        except Exception as e:
            row = {
                "ts": utc_now_iso(),
                "source": "btc15m-arb",
                "action": "SKIP",
                "reason": "quote_fetch_error",
                "market_id": mid,
                "error": str(e)[:200],
                "live": live,
            }
            append_journal(row)
            continue

        # Decide side based on fair_prob vs market mid (use best_yes as proxy)
        if q.best_yes is None:
            row = {
                "ts": utc_now_iso(),
                "source": "btc15m-arb",
                "action": "SKIP",
                "reason": "no_yes_quote",
                "market_id": mid,
                "question": q.question,
                "live": live,
            }
            append_journal(row)
            continue

        mkt_prob = float(q.best_yes)
        side = "YES" if fair_prob > mkt_prob else "NO"
        edge = abs(fair_prob - mkt_prob)

        if edge < edge_min:
            row = {
                "ts": utc_now_iso(),
                "source": "btc15m-arb",
                "action": "SKIP",
                "reason": "edge_too_small",
                "market_id": mid,
                "question": q.question,
                "mkt_yes": mkt_prob,
                "fair": fair_prob,
                "edge": edge,
                "conf": confidence,
                "live": live,
            }
            append_journal(row)
            continue

        if confidence < conf_min:
            row = {
                "ts": utc_now_iso(),
                "source": "btc15m-arb",
                "action": "SKIP",
                "reason": "confidence_too_low",
                "market_id": mid,
                "question": q.question,
                "edge": edge,
                "conf": confidence,
                "live": live,
            }
            append_journal(row)
            continue

        # Liquidity: TODO - fetch book depth from SDK endpoint; placeholder passes paper threshold only.
        depth_usd = float(os.environ.get("SIMMER_BTC_ARB_DEPTH_USD_OVERRIDE", "0") or 0)
        if depth_usd and depth_usd < liq_min:
            row = {
                "ts": utc_now_iso(),
                "source": "btc15m-arb",
                "action": "SKIP",
                "reason": "liquidity_too_low",
                "market_id": mid,
                "depth_usd": depth_usd,
                "liq_min": liq_min,
                "live": live,
            }
            append_journal(row)
            continue

        # Sizing
        px = mkt_prob if side == "YES" else (1 - mkt_prob)
        desired_usd = max_pos
        shares = shares_for_usd(desired_usd, px)

        bump_needed = 0.0
        if shares < min_shares:
            usd_for_min = min_shares * px
            bump_needed = usd_for_min
            bump_cap = min(max_pos, max_bump)
            if usd_for_min > bump_cap:
                row = {
                    "ts": utc_now_iso(),
                    "source": "btc15m-arb",
                    "action": "SKIP",
                    "reason": "min_shares_bump_exceeds_cap",
                    "market_id": mid,
                    "side": side,
                    "price": px,
                    "shares": shares,
                    "min_shares": min_shares,
                    "usd_for_min": usd_for_min,
                    "bump_cap": bump_cap,
                    "live": live,
                }
                append_journal(row)
                continue
            desired_usd = usd_for_min
            shares = min_shares

        # Intent cooldown per market to avoid spamming paper entries
        intent_cd = int(os.environ.get("SIMMER_BTC_ARB_INTENT_COOLDOWN_SECS", "900"))
        st = load_state()
        li = st.get("last_intent_ts", {})
        now_ts = time.time()
        last_ts = float(li.get(mid) or 0)
        if intent_cd > 0 and (now_ts - last_ts) < intent_cd:
            row = {
                "ts": utc_now_iso(),
                "source": "btc15m-arb",
                "action": "SKIP",
                "reason": "intent_cooldown",
                "market_id": mid,
                "question": q.question,
                "secs_remaining": int(intent_cd - (now_ts - last_ts)),
                "live": live,
            }
            append_journal(row)
            continue

        row = {
            "ts": utc_now_iso(),
            "source": "btc15m-arb",
            "action": "TRADE_INTENT" if live else "PAPER_INTENT",
            "market_id": mid,
            "question": q.question,
            "side": side,
            "mkt_yes": mkt_prob,
            "fair": fair_prob,
            "edge": edge,
            "conf": confidence,
            "price": px,
            "usd": desired_usd,
            "shares": shares,
            "bump_needed_usd": bump_needed,
            "live": live,
        }
        append_journal(row)
        # update state
        st = load_state()
        st.setdefault("last_intent_ts", {})[mid] = now_ts
        save_state(st)

        if not args.quiet:
            print(json.dumps(row, ensure_ascii=False))

        # Live execution TODO: integrate simmer_sdk buy.

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
