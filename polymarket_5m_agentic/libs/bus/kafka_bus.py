from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator, Optional

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .events import Event


class KafkaBus:
    def __init__(self, bootstrap: str | None = None):
        self.bootstrap = bootstrap or os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
        self._producer: Optional[AIOKafkaProducer] = None

    async def start(self) -> None:
        if self._producer:
            return
        self._producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap)
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: str, event: Event) -> None:
        assert self._producer, "KafkaBus not started"
        key = event.key.encode()
        value = orjson.dumps({
            "type": event.type,
            "ts_ms": event.ts_ms,
            "source": event.source,
            "key": event.key,
            "payload": event.payload,
        })
        await self._producer.send_and_wait(topic, value=value, key=key)

    async def subscribe(self, topic: str, group_id: str) -> AsyncIterator[Event]:
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self.bootstrap,
            group_id=group_id,
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        await consumer.start()
        try:
            async for msg in consumer:
                data = orjson.loads(msg.value)
                yield Event(
                    type=data["type"],
                    ts_ms=int(data["ts_ms"]),
                    source=data["source"],
                    key=data["key"],
                    payload=data["payload"],
                )
        finally:
            await consumer.stop()


async def run_forever(coro):
    while True:
        try:
            await coro()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1)
