from __future__ import annotations

import asyncio
import json

import websockets

from libs.bus.events import Event
from libs.bus.kafka_bus import KafkaBus
from libs.utils.config import load_config
from libs.utils.time import now_ms


TOPIC_TICKS = "ticks"


async def binance_bookticker_publisher(bus: KafkaBus) -> None:
    cfg = load_config()
    url = cfg.binance_ws
    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        async for msg in ws:
            data = json.loads(msg)
            # bookTicker: {u,s,b,B,a,A}
            evt = Event(
                type="tick",
                ts_ms=now_ms(),
                source="binance",
                key=data.get("s", "BTCUSDT"),
                payload={
                    "bid": float(data["b"]),
                    "ask": float(data["a"]),
                    "bid_qty": float(data["B"]),
                    "ask_qty": float(data["A"]),
                },
            )
            await bus.publish(TOPIC_TICKS, evt)


async def main() -> None:
    bus = KafkaBus()
    await bus.start()
    await binance_bookticker_publisher(bus)


if __name__ == "__main__":
    asyncio.run(main())
