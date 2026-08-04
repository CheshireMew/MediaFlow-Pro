from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import TracebackType


class ProjectCommandQueue:
    """FIFO, re-entrant execution boundary for one project's writes."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._next_ticket = 0
        self._serving_ticket = 0
        self._owner_thread_id: int | None = None
        self._owner_depth = 0
        self._entry_scopes = threading.local()

    @contextmanager
    def command(self) -> Iterator[None]:
        thread_id = threading.get_ident()
        with self._condition:
            if self._owner_thread_id == thread_id:
                self._owner_depth += 1
                reentrant = True
            else:
                ticket = self._next_ticket
                self._next_ticket += 1
                while (
                    ticket != self._serving_ticket
                    or self._owner_thread_id is not None
                ):
                    self._condition.wait()
                self._owner_thread_id = thread_id
                self._owner_depth = 1
                reentrant = False
        try:
            yield
        finally:
            with self._condition:
                if self._owner_thread_id != thread_id:
                    raise RuntimeError(
                        "Project command queue ownership changed during execution"
                    )
                self._owner_depth -= 1
                if self._owner_depth == 0:
                    self._owner_thread_id = None
                    if not reentrant:
                        self._serving_ticket += 1
                    self._condition.notify_all()

    def __enter__(self) -> ProjectCommandQueue:
        scope = self.command()
        scope.__enter__()
        stack = list(getattr(self._entry_scopes, "stack", ()))
        stack.append(scope)
        self._entry_scopes.stack = stack
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        stack = list(getattr(self._entry_scopes, "stack", ()))
        if not stack:
            raise RuntimeError("Project command queue exit has no matching entry")
        scope = stack.pop()
        self._entry_scopes.stack = stack
        return scope.__exit__(error_type, error, traceback)
