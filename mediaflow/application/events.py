from __future__ import annotations

import logging
import threading
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


TaskEventHandler = Callable[[TaskEvent], None]
SnapshotProvider = Callable[[], Iterable[TaskEvent]]


class TaskEventBus:
    """Thread-safe in-process task stream with snapshot-before-events semantics."""

    def __init__(self, snapshot_provider: SnapshotProvider | None = None):
        self._snapshot_provider = snapshot_provider or (lambda: ())
        self._subscribers: dict[int, TaskEventHandler] = {}
        self._next_token = 1
        self._lock = threading.RLock()

    def subscribe(self, handler: TaskEventHandler, *, include_snapshot: bool = True) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._subscribers[token] = handler
            snapshot = tuple(self._snapshot_provider()) if include_snapshot else ()
        for event in snapshot:
            self._deliver(handler, event)
        return token

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def publish(self, event: TaskEvent) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers.values())
        for handler in subscribers:
            self._deliver(handler, event)

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
