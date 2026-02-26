from __future__ import annotations

import asyncio

from libs.bus.kafka_bus import KafkaBus

TOPIC_ORDER_INTENTS = "order_intents"


async def main() -> None:
    bus = KafkaBus()
    await bus.start()

    async for evt in bus.subscribe(TOPIC_ORDER_INTENTS, group_id="exec"):
        intent = evt.payload
        if intent.get("action") != "PLACE":
            continue

        # TODO: integrate Polymarket CLOB client + signing + idempotency.
        # For now, just print.
        print(f"[EXEC] would place order: {intent}")


if __name__ == "__main__":
    asyncio.run(main())
