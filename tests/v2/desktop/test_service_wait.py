from __future__ import annotations

import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from mediaflow.desktop.service_wait import wait_for_service_future


def test_gui_service_wait_keeps_queued_desktop_events_alive() -> None:
    _application = QApplication.instance() or QApplication([])
    future: Future[str] = Future()
    delivered: list[str] = []
    pulse = QTimer()
    pulse.setInterval(1)
    pulse.timeout.connect(lambda: delivered.append("pulse"))
    pulse.start()
    completion = threading.Timer(0.05, lambda: future.set_result("ready"))
    completion.start()
    try:
        assert wait_for_service_future(future, 1.0) == "ready"
    finally:
        pulse.stop()
        completion.cancel()
    assert delivered


def test_gui_service_wait_has_a_real_deadline() -> None:
    _application = QApplication.instance() or QApplication([])
    future: Future[None] = Future()
    with pytest.raises(FutureTimeoutError):
        wait_for_service_future(future, 0.01)
