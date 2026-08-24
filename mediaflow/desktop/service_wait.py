from __future__ import annotations

import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from PySide6.QtCore import QCoreApplication, QEventLoop, QThread

from mediaflow.service.client import install_sync_future_waiter

_EVENT_PUMP_SLICE_MS = 5
_TRANSPORT_WAIT_SLICE_SECONDS = 0.01


def wait_for_service_future(future: Future[Any], timeout_seconds: float) -> Any:
    """Wait without starving the desktop event loop.

    Service I/O always runs on the dedicated transport thread. On the GUI
    thread this local loop continues paint, timer, and queued-signal delivery,
    but excludes new user input until the command has a typed result. Worker
    threads retain the normal blocking Future contract.
    """

    application = QCoreApplication.instance()
    if application is None or QThread.currentThread() is not application.thread():
        return future.result(timeout=timeout_seconds)
    if future.done():
        return future.result(timeout=0)

    deadline = time.monotonic() + timeout_seconds
    flags = QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FutureTimeoutError()
        try:
            return future.result(
                timeout=min(_TRANSPORT_WAIT_SLICE_SECONDS, remaining),
            )
        except FutureTimeoutError:
            # A completed transport may itself carry a timeout exception. Do
            # not mistake that result for the short polling deadline.
            if future.done():
                return future.result(timeout=0)
        # Let the transport thread run without a 1 ms GUI-thread busy poll,
        # then give Qt one bounded paint/timer/queued-signal turn. This keeps
        # the window responsive at frame cadence without starving the RPC.
        application.processEvents(
            flags,
            min(_EVENT_PUMP_SLICE_MS, max(1, round(remaining * 1_000))),
        )


def install_desktop_service_waiter() -> None:
    install_sync_future_waiter(wait_for_service_future)
