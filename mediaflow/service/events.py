from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ServiceEvent:
    type: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "payload": self.payload}


class EventHub:
    """Fan-out for committed events; callers publish only after durable commit."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._subscribers: dict[int, asyncio.Queue[ServiceEvent]] = {}
        self._serial = 0

    def subscribe(self) -> tuple[int, asyncio.Queue[ServiceEvent]]:
        self._serial += 1
        queue: asyncio.Queue[ServiceEvent] = asyncio.Queue()
        self._subscribers[self._serial] = queue
        return self._serial, queue

    def unsubscribe(self, subscription: int) -> None:
        self._subscribers.pop(subscription, None)

    def publish_from_worker(self, event: ServiceEvent) -> None:
        self._loop.call_soon_threadsafe(self._publish, event)

    def publish(self, event: ServiceEvent) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("EventHub.publish must run on its owning loop")
        self._publish(event)

    def publisher(self, event_type: str) -> Callable[[dict[str, Any]], None]:
        return lambda payload: self.publish_from_worker(ServiceEvent(event_type, payload))

    def _publish(self, event: ServiceEvent) -> None:
        for queue in tuple(self._subscribers.values()):
            queue.put_nowait(event)
