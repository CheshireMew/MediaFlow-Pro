from __future__ import annotations

import threading

from mediaflow.application.task_waiter import TaskStateWaiter
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.task_commands import AnalyzeDownloadCommand
from mediaflow.domain.tasks import Task


class _TaskLookup:
    def __init__(self, task: Task) -> None:
        self.task = task
        self.reads = 0
        self.first_read = threading.Event()

    def get(self, task_id: str) -> Task:
        assert task_id == self.task.id
        self.reads += 1
        self.first_read.set()
        return self.task.model_copy(deep=True)


def test_task_waiter_sleeps_until_a_committed_state_change_notifies_it() -> None:
    pending = Task(
        project_id="project",
        command=AnalyzeDownloadCommand(url="test://task-waiter"),
    )
    lookup = _TaskLookup(pending)
    waiter = TaskStateWaiter(lookup)
    observed: list[Task] = []
    thread = threading.Thread(
        target=lambda: observed.append(waiter.wait(pending.id, timeout=2)),
    )

    thread.start()
    assert lookup.first_read.wait(1)
    assert lookup.reads == 1

    lookup.task = pending.model_copy(update={"status": TaskStatus.COMPLETED})
    waiter.notify()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert lookup.reads == 2
    assert observed[0].status == TaskStatus.COMPLETED
