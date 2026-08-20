from __future__ import annotations

import builtins
from collections.abc import Callable

from mediaflow.application.events import TaskEvent
from mediaflow.application.ports import TaskStore
from mediaflow.application.task_execution import TaskExecutionEngine
from mediaflow.application.task_waiter import TaskStateWaiter
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.task_commands import TaskCommand
from mediaflow.domain.tasks import Task

TaskPublisher = Callable[[Task, str], None]


class TaskLifecycle:
    """Owns user-visible task creation, control, queries and history cleanup."""

    def __init__(
        self,
        repository: TaskStore,
        execution: TaskExecutionEngine,
        waiter: TaskStateWaiter,
        publish: TaskPublisher,
    ) -> None:
        self.repository = repository
        self.execution = execution
        self.waiter = waiter
        self.publish = publish

    def start(
        self,
        *,
        project_id: str,
        command: TaskCommand,
        sequence_id: str | None = None,
        input_asset_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        self._require_writable()
        command_sequence_id = getattr(command, "sequence_id", None)
        if isinstance(command_sequence_id, str):
            if sequence_id is not None and sequence_id != command_sequence_id:
                raise ValueError("Task sequence must match its command sequence")
            sequence_id = command_sequence_id
        with self.execution.lock:
            self.execution.require_accepting()
            command.validate_for_execution()
            kind = command.task_kind
            if kind not in self.execution.handlers:
                raise KeyError(f"No task handler registered for {kind.value}")
            candidate = Task(
                project_id=project_id,
                sequence_id=sequence_id,
                idempotency_key=idempotency_key,
                command=command,
                input_asset_ids=input_asset_ids or [],
            )
            task = self.repository.create(candidate, event_type="created")
            if task.id == candidate.id:
                self.publish(task, "created")
            if task.status.is_in_flight:
                self.execution.schedule(task)
            return task

    def resume(
        self,
        task_id: str,
        *,
        allow_existing: bool = False,
    ) -> Task:
        self._require_writable()
        with self.execution.lock:
            self.execution.require_accepting()
            task = self.repository.get(task_id)
            if not self.execution.has_handler(task):
                raise KeyError(f"No task handler registered for {task.kind.value}")
            resumed = self.repository.queue_paused(task_id)
            if resumed is None:
                current = self.repository.get(task_id)
                if allow_existing and (
                    current.status.is_active or current.status.is_consumable
                ):
                    if current.status.is_in_flight:
                        self.execution.schedule(current)
                    return current
                raise ValueError("Only paused tasks can be resumed")
            self.publish(resumed, "status")
            self.execution.schedule(resumed)
            return resumed

    def retry(self, task_id: str) -> Task:
        self._require_writable()
        task = self.repository.get(task_id)
        if not task.status.is_retryable:
            raise ValueError("Only failed or cancelled tasks can be retried")
        return self.start(
            project_id=task.project_id,
            sequence_id=task.sequence_id,
            command=task.command,
            input_asset_ids=list(task.input_asset_ids),
        )

    def pause(self, task_id: str) -> None:
        self._require_writable()
        task = self.repository.get(task_id)
        if task.status == TaskStatus.PAUSED:
            return
        if not task.status.is_in_flight:
            raise ValueError("Only pending or running tasks can be paused")
        requested = self.repository.request_stop(task_id, "pause")
        if requested is not None:
            self.publish(
                requested,
                "control" if requested.status == TaskStatus.RUNNING else "status",
            )
        self.execution.request_pause(task_id)

    def cancel(self, task_id: str) -> None:
        self._require_writable()
        requested = self.repository.request_stop(task_id, "cancel")
        if requested is not None:
            self.publish(
                requested,
                "control" if requested.status == TaskStatus.RUNNING else "status",
            )
        else:
            current = self.repository.get(task_id)
            if not current.status.is_active:
                raise ValueError("Only active tasks can be cancelled")
        self.execution.request_cancel(task_id)

    def pause_all(self) -> int:
        self._require_writable()
        count = 0
        for task in self.repository.list():
            if task.status.is_in_flight:
                self.pause(task.id)
                count += 1
        return count

    def cancel_all(self) -> int:
        self._require_writable()
        count = 0
        for task in self.repository.list():
            if not task.status.is_active:
                continue
            requested = self.repository.request_stop(task.id, "cancel")
            if requested is None:
                continue
            self.publish(
                requested,
                "control" if requested.status == TaskStatus.RUNNING else "status",
            )
            self.execution.request_cancel(task.id)
            count += 1
        return count

    def delete(self, task_id: str) -> None:
        self._require_writable()
        task = self.repository.get(task_id)
        if not task.status.is_terminal:
            raise ValueError("Only completed, failed, or cancelled tasks can be removed")
        self.repository.delete(task_id, event_type="deleted")
        self.execution.forget_task(task_id)
        self.publish(task, "deleted")

    def clear_history(self) -> int:
        self._require_writable()
        tasks = self.repository.delete_terminal()
        for task in tasks:
            self.execution.forget_task(task.id)
            self.publish(task, "deleted")
        return len(tasks)

    def wait(self, task_id: str, timeout: float | None = None) -> Task:
        return self.waiter.wait(task_id, timeout)

    def get(self, task_id: str) -> Task:
        return self.repository.get(task_id)

    def list(self) -> builtins.list[Task]:
        return self.repository.list()

    def snapshot(self) -> tuple[builtins.list[Task], int]:
        return self.repository.snapshot()

    def events_after(
        self,
        cursor: int,
        *,
        limit: int = 500,
    ) -> builtins.list[TaskEvent]:
        return self.repository.events_after(cursor, limit=limit)

    def _require_writable(self) -> None:
        if self.repository.read_only:
            raise PermissionError("Project task service is read-only")
