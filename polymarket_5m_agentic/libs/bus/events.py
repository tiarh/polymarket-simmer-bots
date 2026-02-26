from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


EventType = Literal[
    "tick",
    "orderbook",
    "market",
    "spread",
    "signal",
    "risk",
    "order_intent",
    "execution",
]


@dataclass(frozen=True)
class Event:
    type: EventType
    ts_ms: int
    source: str
    key: str
    payload: dict[str, Any]
