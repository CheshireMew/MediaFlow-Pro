from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from mediaflow.operation_cancellation import OperationCancelled


class PresentationRequestCoordinator:
    """Cancel superseded presentation work, including requests still awaiting admission."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: dict[tuple[str, str], tuple[int, bool]] = {}

    @contextmanager
    def request(self, key: tuple[str, str], generation: int) -> Iterator[Callable[[], None]]:
        with self._lock:
            current = self._current.get(key)
            if current is None or generation > current[0]:
                self._current[key] = (generation, False)

        def check_cancelled() -> None:
            with self._lock:
                current = self._current.get(key)
            if current != (generation, False):
                raise OperationCancelled("Presentation request was superseded")

        check_cancelled()
        yield check_cancelled

    def cancel(self, key: tuple[str, str], generation: int) -> None:
        with self._lock:
            current = self._current.get(key)
            if current is None or generation > current[0]:
                self._current[key] = (generation, False)

    def cancel_current(self, key: tuple[str, str]) -> None:
        with self._lock:
            current = self._current.get(key)
            if current is not None:
                self._current[key] = (current[0], True)

    def cancel_project(self, project: str) -> None:
        with self._lock:
            for key in tuple(self._current):
                if key[0] == project:
                    self._current[key] = (self._current[key][0], True)
