from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

EVENT_QUEUE_CAPACITY = 256
EVENT_STREAM_OVERFLOW = "service.resync_required"


@dataclass(frozen=True, slots=True)
class ServiceEvent:
    type: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "payload": self.payload}


EventSelector = Callable[[ServiceEvent], bool]


@dataclass(slots=True)
class _Subscription:
    queue: asyncio.Queue[ServiceEvent]
    selector: EventSelector
    overflowed: bool = False


class EventHub:
    """Bounded, filtered fan-out for events published after durable commit.

    Project and task events are durable. If a slow subscriber exhausts its
    bounded queue, the stream asks that subscriber to reconnect and replay from
    its last committed cursors instead of silently dropping a state change.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._subscribers: dict[int, _Subscription] = {}
        self._serial = 0

    def subscribe(
        self,
        *,
        selector: EventSelector | None = None,
        capacity: int = EVENT_QUEUE_CAPACITY,
    ) -> tuple[int, asyncio.Queue[ServiceEvent]]:
        if capacity < 1:
            raise ValueError("Event subscription capacity must be positive")
        self._serial += 1
        queue: asyncio.Queue[ServiceEvent] = asyncio.Queue(maxsize=capacity)
        self._subscribers[self._serial] = _Subscription(
            queue=queue,
            selector=selector or (lambda _event: True),
        )
        return self._serial, queue

    def replace_selector(
        self,
        subscription: int,
        selector: EventSelector,
        *,
        discard_pending: bool = True,
    ) -> None:
        subscriber = self._subscribers.get(subscription)
        if subscriber is None:
            raise ValueError(f"Unknown event subscription: {subscription}")
        subscriber.selector = selector
        subscriber.overflowed = False
        if discard_pending:
            self._clear(subscriber.queue)

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
        for subscription, subscriber in tuple(self._subscribers.items()):
            if event.type == "service.stopping":
                self._clear(subscriber.queue)
                subscriber.queue.put_nowait(event)
                subscriber.overflowed = True
                continue
            if subscriber.overflowed or not subscriber.selector(event):
                continue
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                self._clear(subscriber.queue)
                subscriber.queue.put_nowait(
                    ServiceEvent(
                        EVENT_STREAM_OVERFLOW,
                        {
                            "reason": "subscriber_queue_full",
                            "subscription": subscription,
                            "capacity": subscriber.queue.maxsize,
                            "recovery": "reconnect_with_committed_cursors",
                        },
                    )
                )
                subscriber.overflowed = True

    @staticmethod
    def _clear(queue: asyncio.Queue[ServiceEvent]) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
