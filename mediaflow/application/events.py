from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TaskEvent:
    task_id: str
    project_id: str
    event_type: str
    revision: int
    payload: dict[str, Any] = field(default_factory=dict)
    cursor: int = 0


TaskEventHandler = Callable[[TaskEvent], None]
SnapshotProvider = Callable[[], Iterable[TaskEvent]]


@dataclass(slots=True)
class _Subscriber:
    handler: TaskEventHandler
    queue: deque[TaskEvent] = field(default_factory=deque)
    initializing: bool = True
    delivering: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


class TaskEventBus:
    """Thread-safe in-process task stream with snapshot-before-events semantics."""

    def __init__(self, snapshot_provider: SnapshotProvider | None = None):
        self._snapshot_provider = snapshot_provider or (lambda: ())
        self._subscribers: dict[int, _Subscriber] = {}
        self._next_token = 1
        self._lock = threading.RLock()

    def subscribe(self, handler: TaskEventHandler, *, include_snapshot: bool = True) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            snapshot = tuple(self._snapshot_provider()) if include_snapshot else ()
            subscriber = _Subscriber(handler=handler, queue=deque(snapshot))
            self._subscribers[token] = subscriber
        self._activate(subscriber)
        return token

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def publish(self, event: TaskEvent) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers.values())
        for subscriber in subscribers:
            self._enqueue(subscriber, event)

    def _activate(self, subscriber: _Subscriber) -> None:
        with subscriber.lock:
            subscriber.initializing = False
            self._drain(subscriber)

    def _enqueue(self, subscriber: _Subscriber, event: TaskEvent) -> None:
        with subscriber.lock:
            subscriber.queue.append(event)
            if subscriber.initializing:
                return
            self._drain(subscriber)

    def _drain(self, subscriber: _Subscriber) -> None:
        if subscriber.delivering:
            return
        subscriber.delivering = True
        try:
            while subscriber.queue:
                self._deliver(subscriber.handler, subscriber.queue.popleft())
        finally:
            subscriber.delivering = False

    @staticmethod
    def _deliver(handler: TaskEventHandler, event: TaskEvent) -> None:
        try:
            handler(event)
        except Exception:
            # Observers are projections of persisted task state. A broken UI or
            # telemetry observer must never be able to rewrite a successful task.
            logger.exception(
                "Task event observer failed (task=%s, revision=%s, type=%s)",
                event.task_id,
                event.revision,
                event.event_type,
            )

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
