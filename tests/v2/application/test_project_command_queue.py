from __future__ import annotations

import threading
import time

from mediaflow.application.project_command_queue import ProjectCommandQueue


def test_project_command_queue_is_fifo_and_reentrant() -> None:
    queue = ProjectCommandQueue()
    first_entered = threading.Event()
    release_first = threading.Event()
    order: list[str] = []

    def first() -> None:
        with queue:
            order.append("first")
            first_entered.set()
            assert release_first.wait(5)
            with queue:
                order.append("first-nested")

    def queued(label: str) -> None:
        with queue:
            order.append(label)

    first_thread = threading.Thread(target=first)
    first_thread.start()
    assert first_entered.wait(5)
    second_thread = threading.Thread(target=queued, args=("second",))
    third_thread = threading.Thread(target=queued, args=("third",))
    second_thread.start()
    time.sleep(0.02)
    third_thread.start()
    time.sleep(0.02)
    release_first.set()

    for thread in (first_thread, second_thread, third_thread):
        thread.join(5)
        assert not thread.is_alive()

    assert order == ["first", "first-nested", "second", "third"]
