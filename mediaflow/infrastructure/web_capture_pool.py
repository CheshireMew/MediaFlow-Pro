from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class Closable(Protocol):
    def close(self) -> None: ...


class IdleResourcePool[Resource: Closable]:
    """Reference-count shared resources and retire them after an idle window."""

    def __init__(self) -> None:
        self._resources: dict[Path, Resource] = {}
        self._leases: dict[Path, int] = {}
        self._timers: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()

    def acquire(self, key: Path, factory: Callable[[], Resource]) -> Resource:
        with self._lock:
            timer = self._timers.pop(key, None)
            if timer is not None:
                timer.cancel()
            resource = self._resources.get(key)
            if resource is None:
                resource = factory()
                self._resources[key] = resource
            self._leases[key] = self._leases.get(key, 0) + 1
            return resource

    def release(self, key: Path, resource: Resource, *, idle_seconds: float) -> None:
        with self._lock:
            if self._resources.get(key) is not resource:
                return
            leases = self._leases.get(key, 0)
            if leases <= 0:
                return
            if leases > 1:
                self._leases[key] = leases - 1
                return
            self._leases.pop(key, None)
            previous = self._timers.pop(key, None)
            if previous is not None:
                previous.cancel()

            def retire() -> None:
                closing = None
                with self._lock:
                    if self._timers.get(key) is not timer:
                        return
                    self._timers.pop(key, None)
                    if self._resources.get(key) is resource and not self._leases.get(key, 0):
                        closing = self._resources.pop(key)
                if closing is not None:
                    closing.close()

            timer = threading.Timer(idle_seconds, retire)
            timer.daemon = True
            self._timers[key] = timer
        timer.start()

    def peek(self, key: Path) -> Resource | None:
        with self._lock:
            return self._resources.get(key)

    def close_all(self) -> None:
        with self._lock:
            timers = list(self._timers.values())
            resources = list(self._resources.values())
            self._timers.clear()
            self._leases.clear()
            self._resources.clear()
        for timer in timers:
            timer.cancel()
        for resource in resources:
            resource.close()
