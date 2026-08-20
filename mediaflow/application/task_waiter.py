from __future__ import annotations

import threading
import time
from typing import Protocol

from mediaflow.domain.tasks import Task


class TaskLookup(Protocol):
    def get(self, task_id: str) -> Task: ...


class TaskStateWaiter:
    """Wait for persisted task state changes without polling the repository."""

    def __init__(self, tasks: TaskLookup) -> None:
        self._tasks = tasks
        self._changed = threading.Condition()

    def notify(self) -> None:
        with self._changed:
            self._changed.notify_all()

    def wait(self, task_id: str, timeout: float | None = None) -> Task:
        if timeout is not None and timeout < 0:
            raise ValueError("Task wait timeout cannot be negative")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._changed:
            while True:
                task = self._tasks.get(task_id)
                if task.status.is_settled:
                    return task
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(
                        f"Task {task_id} did not finish within {timeout} seconds"
                    )
                self._changed.wait(timeout=remaining)
