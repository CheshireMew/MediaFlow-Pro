from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path

from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.tasks import ArtifactReference, Task, TaskOutcome


class TaskStopped(RuntimeError):
    def __init__(self, status: TaskStatus):
        super().__init__(status.value)
        self.status = status


class TaskLeaseLost(RuntimeError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested: TaskStatus | None = None
        self._lease_lost = False

    def request_pause(self) -> None:
        with self._lock:
            if self._requested != TaskStatus.CANCELLED:
                self._requested = TaskStatus.PAUSED

    def request_cancel(self) -> None:
        with self._lock:
            self._requested = TaskStatus.CANCELLED

    def request_lease_lost(self) -> None:
        with self._lock:
            self._lease_lost = True

    def raise_if_requested(self) -> None:
        with self._lock:
            requested = self._requested
            lease_lost = self._lease_lost
        if lease_lost:
            raise TaskLeaseLost("Task execution lease was claimed by another owner")
        if requested is not None:
            raise TaskStopped(requested)


ProgressReporter = Callable[[OperationProgress], None]


@dataclass(slots=True)
class TaskContext:
    task: Task
    project_dir: Path
    cancellation: CancellationToken
    report: ProgressReporter
    recovered: bool = False
    _project_changes: list[Callable[[], None]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _project_change_committer: Callable[[Callable[[], None]], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _project_change_committed: bool = field(
        default=False,
        init=False,
        repr=False,
    )

    def defer_project_change(self, change: Callable[[], None]) -> None:
        if self._project_change_committed:
            raise RuntimeError(
                "A task cannot mix prerequisite and terminal project commands"
            )
        self._project_changes.append(change)

    def project_changes(self) -> tuple[Callable[[], None], ...]:
        return tuple(self._project_changes)

    def commit_project_change(self, change: Callable[[], None]) -> None:
        if self._project_change_committed:
            raise RuntimeError(
                "A task may submit only one prerequisite project command"
            )
        if self._project_changes:
            raise RuntimeError(
                "A task cannot mix prerequisite and terminal project commands"
            )
        self._project_change_committed = True
        if self._project_change_committer is None:
            change()
        else:
            self._project_change_committer(change)


@dataclass(frozen=True, slots=True)
class TaskCompletion:
    artifacts: tuple[ArtifactReference, ...] = ()
    outcome: TaskOutcome | None = None

    @classmethod
    def with_artifacts(
        cls,
        *artifacts: ArtifactReference,
        outcome: TaskOutcome | None = None,
    ) -> TaskCompletion:
        return cls(artifacts=artifacts, outcome=outcome)


TaskHandler = Callable[[TaskContext], TaskCompletion]
TaskPreparationScope = Callable[[Task], AbstractContextManager[None]]
TaskSettlementPersistence = Callable[[], Task]
TaskSettlementCommitter = Callable[
    [Task, TaskSettlementPersistence, tuple[Callable[[], None], ...]],
    Task,
]
TaskProjectChangeCommitter = Callable[[Task, Callable[[], None]], None]


def task_kind_handler(
    handlers: dict[TaskKind, TaskHandler],
    task: Task,
) -> TaskHandler:
    try:
        return handlers[task.kind]
    except KeyError as error:
        raise KeyError(f"No task handler registered for {task.kind.value}") from error
