from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from libs.bus.events import Event
from libs.bus.kafka_bus import KafkaBus
from libs.utils.config import load_config

TOPIC_TICKS = "ticks"
TOPIC_SIGNALS = "signals"
TOPIC_ORDER_INTENTS = "order_intents"


class OrchestratorState(TypedDict, total=False):
    tick: dict[str, Any]
    research: dict[str, Any]
    signal: dict[str, Any]
    risk: dict[str, Any]
    order_intent: dict[str, Any]


def spread_detector(state: OrchestratorState) -> OrchestratorState:
    """Compute simple spread / edge placeholder.

    Replace with:
    - Polymarket YES/NO mid from CLOB orderbook
    - CEX mid from multiple feeds
    - Fee-aware edge in bps
    - Lag estimation
    """
    tick = state["tick"]
    bid = tick["bid"]
    ask = tick["ask"]
    cex_mid = (bid + ask) / 2

    # Placeholder Polymarket probability estimate from CEX momentum/etc.
    pm_yes = 0.50
    edge_yes_bps = (pm_yes - 0.50) * 10_000

    state["research"] = {
        "cex_mid": cex_mid,
        "pm_yes": pm_yes,
        "edge_yes_bps": edge_yes_bps,
        "liquidity_usd": 50_000,
        "lag_ms": 300,
    }
    return state


def signal_generator(state: OrchestratorState) -> OrchestratorState:
    cfg = load_config()
    r = state["research"]

    edge_bps = r["edge_yes_bps"]
    confidence = 0.65 if abs(edge_bps) >= cfg.risk.min_edge_bps else 0.40

    action = "TRADE" if (abs(edge_bps) >= cfg.risk.min_edge_bps and confidence >= cfg.risk.min_confidence) else "SKIP"

    state["signal"] = {
        "side": "YES" if edge_bps > 0 else "NO",
        "edge_bps": edge_bps,
        "confidence": confidence,
        "action": action,
    }
    return state


def risk_filter(state: OrchestratorState) -> OrchestratorState:
    cfg = load_config()
    r = state["research"]
    sig = state["signal"]

    ok = True
    reasons: list[str] = []

    if r.get("liquidity_usd", 0) < cfg.risk.liquidity_min_usd:
        ok = False
        reasons.append("liquidity")
    if sig["action"] != "TRADE":
        ok = False
        reasons.append("no_edge")

    state["risk"] = {"ok": ok, "reasons": reasons}
    return state


def build_order_intent(state: OrchestratorState) -> OrchestratorState:
    sig = state["signal"]
    risk = state["risk"]

    if not risk["ok"]:
        state["order_intent"] = {"action": "NONE", "reason": risk["reasons"]}
        return state

    # Placeholder sizing; replace with Kelly-capped sizing and portfolio limits.
    size_usd = 100.0

    state["order_intent"] = {
        "action": "PLACE",
        "market_id": "BTC_5MIN_PLACEHOLDER",
        "side": sig["side"],
        "price": 0.50,
        "size_usd": size_usd,
    }
    return state


def compile_graph():
    g = StateGraph(OrchestratorState)
    g.add_node("spread", spread_detector)
    g.add_node("signal", signal_generator)
    g.add_node("risk", risk_filter)
    g.add_node("intent", build_order_intent)

    g.set_entry_point("spread")
    g.add_edge("spread", "signal")
    g.add_edge("signal", "risk")
    g.add_edge("risk", "intent")
    g.add_edge("intent", END)
    return g.compile()


@dataclass
class Tick:
    bid: float
    ask: float


async def main() -> None:
    bus = KafkaBus()
    await bus.start()
    graph = compile_graph()

    async for evt in bus.subscribe(TOPIC_TICKS, group_id="orchestrator"):
        if evt.type != "tick" or evt.source != "binance":
            continue

        state: OrchestratorState = {"tick": evt.payload}
        out = graph.invoke(state)

        # publish signal + order intent
        await bus.publish(TOPIC_SIGNALS, Event(type="signal", ts_ms=evt.ts_ms, source="orchestrator", key=evt.key, payload=out.get("signal", {})))
        await bus.publish(TOPIC_ORDER_INTENTS, Event(type="order_intent", ts_ms=evt.ts_ms, source="orchestrator", key=evt.key, payload=out.get("order_intent", {})))


if __name__ == "__main__":
    asyncio.run(main())
