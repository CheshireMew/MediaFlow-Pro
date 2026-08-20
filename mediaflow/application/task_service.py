from __future__ import annotations

import builtins
import logging
import os
import threading
import time
from uuid import uuid4

from mediaflow.application.events import TaskEvent, TaskEventBus
from mediaflow.application.ports import TaskStore
from mediaflow.application.task_execution import TaskExecutionEngine
from mediaflow.application.task_execution_types import (
    CancellationToken,
    TaskCompletion,
    TaskContext,
    TaskHandler,
    TaskLeaseLost,
    TaskPreparationScope,
    TaskProjectChangeCommitter,
    TaskSettlementCommitter,
    TaskSettlementPersistence,
    TaskStopped,
)
from mediaflow.application.task_lifecycle import TaskLifecycle
from mediaflow.application.task_persistence import TaskPersistence
from mediaflow.application.task_waiter import TaskStateWaiter
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.task_commands import TaskCommand
from mediaflow.domain.tasks import Task

logger = logging.getLogger(__name__)
BACKGROUND_ERROR_LOG_INTERVAL_SECONDS = 30.0

__all__ = (
    "CancellationToken",
    "TaskCompletion",
    "TaskContext",
    "TaskLeaseLost",
    "TaskService",
    "TaskSettlementPersistence",
    "TaskShutdownTimeout",
    "TaskStopped",
)


class TaskShutdownTimeout(TimeoutError):
    def __init__(
        self,
        unfinished_task_ids: tuple[str, ...],
        *,
        maintenance_running: bool,
    ):
        self.unfinished_task_ids = unfinished_task_ids
        self.maintenance_running = maintenance_running
        details: list[str] = []
        if unfinished_task_ids:
            details.append(
                f"{len(unfinished_task_ids)} task worker(s) are still running"
            )
        if maintenance_running:
            details.append("the task recovery worker is still running")
        super().__init__(
            "Task service did not stop before the requested timeout: "
            + ", ".join(details)
        )


class TaskService:
    def __init__(
        self,
        repository: TaskStore,
        *,
        max_workers: int = 3,
        recover_expired: bool = True,
        lease_duration_ms: int = 15_000,
        recovery_poll_interval: float = 1.0,
        execution_owner_id: str | None = None,
        preparation_scope: TaskPreparationScope | None = None,
        project_change_committer: TaskProjectChangeCommitter | None = None,
        settlement_committer: TaskSettlementCommitter | None = None,
    ):
        if lease_duration_ms <= 0:
            raise ValueError("Task lease duration must be positive")
        if recovery_poll_interval <= 0:
            raise ValueError("Task recovery poll interval must be positive")
        self.repository = repository
        self.execution_owner_id = (
            execution_owner_id
            or f"{os.getpid()}:{uuid4()}"
        )
        self._recovery_poll_interval = recovery_poll_interval
        self._recover_expired = recover_expired and not repository.read_only
        self._lock = threading.RLock()
        self._stopping = False
        self._stopped = False
        self._maintenance_stop = threading.Event()
        self._maintenance_thread: threading.Thread | None = None
        self._background_error_log_times: dict[str, float] = {}
        self.events = TaskEventBus(self._snapshot_events)
        self._waiter = TaskStateWaiter(repository)
        self._persistence = TaskPersistence(
            repository,
            execution_owner_id=self.execution_owner_id,
            settlement_committer=settlement_committer,
            publish=self._publish,
            report_background_error=self._log_background_error,
        )
        self._execution = TaskExecutionEngine(
            repository,
            self._persistence,
            execution_owner_id=self.execution_owner_id,
            max_workers=max_workers,
            lease_duration_ms=lease_duration_ms,
            preparation_scope=preparation_scope,
            project_change_committer=project_change_committer,
            publish=self._publish,
            report_background_error=self._log_background_error,
        )
        self._lifecycle = TaskLifecycle(
            repository,
            self._execution,
            self._waiter,
            self._publish,
        )
        if self._recover_expired:
            self._maintenance_thread = threading.Thread(
                target=self._maintenance_loop,
                name="mediaflow-task-recovery",
                daemon=True,
            )
            self._maintenance_thread.start()

    def register(self, kind: TaskKind, handler: TaskHandler) -> None:
        self._execution.register(kind, handler)

    def recover_claimable(self) -> int:
        if not self._recover_expired:
            return 0
        with self._lock:
            if self._stopping:
                return 0
        scheduled = 0
        for task in self.repository.list_claimable(now_ms()):
            if not self._execution.has_handler(task):
                continue
            if self._execution.schedule(task):
                scheduled += 1
        return scheduled

    def start(
        self,
        *,
        project_id: str,
        command: TaskCommand,
        sequence_id: str | None = None,
        input_asset_ids: builtins.list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        return self._lifecycle.start(
            project_id=project_id,
            command=command,
            sequence_id=sequence_id,
            input_asset_ids=input_asset_ids,
            idempotency_key=idempotency_key,
        )

    def resume(
        self,
        task_id: str,
        *,
        allow_existing: bool = False,
    ) -> Task:
        return self._lifecycle.resume(task_id, allow_existing=allow_existing)

    def retry(self, task_id: str) -> Task:
        return self._lifecycle.retry(task_id)

    def pause(self, task_id: str) -> None:
        self._lifecycle.pause(task_id)

    def cancel(self, task_id: str) -> None:
        self._lifecycle.cancel(task_id)

    def pause_all(self) -> int:
        return self._lifecycle.pause_all()

    def cancel_all(self) -> int:
        return self._lifecycle.cancel_all()

    def delete(self, task_id: str) -> None:
        self._lifecycle.delete(task_id)

    def clear_history(self) -> int:
        return self._lifecycle.clear_history()

    def wait(self, task_id: str, timeout: float | None = None) -> Task:
        return self._lifecycle.wait(task_id, timeout)

    def get(self, task_id: str) -> Task:
        return self._lifecycle.get(task_id)

    def list(self) -> builtins.list[Task]:
        return self._lifecycle.list()

    def snapshot(self) -> tuple[builtins.list[Task], int]:
        return self._lifecycle.snapshot()

    def events_after(self, cursor: int, *, limit: int = 500) -> builtins.list[TaskEvent]:
        return self._lifecycle.events_after(cursor, limit=limit)

    def shutdown(self, *, timeout: float | None = None) -> None:
        if timeout is not None and timeout < 0:
            raise ValueError("Task shutdown timeout cannot be negative")
        deadline = (
            None
            if timeout is None
            else time.monotonic() + timeout
        )
        with self._lock:
            if self._stopped:
                return
            self._stopping = True
        owned, futures = self._execution.begin_shutdown()
        self._maintenance_stop.set()
        for task_id, token in owned:
            token.request_pause()
            try:
                task = self.repository.get(task_id)
                if task.status.is_in_flight:
                    requested = self.repository.request_stop(
                        task_id,
                        "pause",
                    )
                    if requested is not None:
                        self._publish(
                            requested,
                            (
                                "control"
                                if requested.status == TaskStatus.RUNNING
                                else "status"
                            ),
                        )
            except (KeyError, ValueError):
                continue
        maintenance_running = False
        if self._maintenance_thread is not None:
            self._maintenance_thread.join(
                timeout=self._remaining_timeout(deadline)
            )
            maintenance_running = self._maintenance_thread.is_alive()
        unfinished_task_ids = self._execution.wait_for_shutdown(
            futures,
            timeout=self._remaining_timeout(deadline),
        )
        if maintenance_running or unfinished_task_ids:
            raise TaskShutdownTimeout(
                unfinished_task_ids,
                maintenance_running=maintenance_running,
            )
        self._execution.close()
        with self._lock:
            self._stopped = True

    def _maintenance_loop(self) -> None:
        while not self._maintenance_stop.wait(
            self._recovery_poll_interval
        ):
            try:
                self.recover_claimable()
            except Exception:
                self._log_background_error(
                    "maintenance",
                    "Task recovery poll failed; the service will retry",
                )
                continue

    def _log_background_error(
        self,
        key: str,
        message: str,
        *,
        level: int = logging.WARNING,
    ) -> None:
        observed_at = time.monotonic()
        with self._lock:
            previous = self._background_error_log_times.get(key)
            if (
                previous is not None
                and observed_at - previous
                < BACKGROUND_ERROR_LOG_INTERVAL_SECONDS
            ):
                return
            self._background_error_log_times[key] = observed_at
        logger.log(
            level,
            message,
            exc_info=True,
        )

    @staticmethod
    def _remaining_timeout(
        deadline: float | None,
    ) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    def _snapshot_events(self) -> builtins.list[TaskEvent]:
        tasks, cursor = self.repository.snapshot()
        return [self._event(task, "snapshot", cursor=cursor) for task in tasks]

    def _publish(self, task: Task, event_type: str) -> None:
        event = self.repository.latest_event(task.id)
        if event.revision > task.revision:
            # A control request may commit between the worker's task write and
            # its in-memory broadcast.  The newer durable event already
            # contains the complete task snapshot, so publishing the older
            # cursor afterwards would make live subscribers move backwards.
            return
        if event.event_type != event_type or event.revision != task.revision:
            raise RuntimeError(f"Persisted task event does not match task {task.id} revision {task.revision}")
        self._waiter.notify()
        self.events.publish(event)

    @staticmethod
    def _event(task: Task, event_type: str, *, cursor: int = 0) -> TaskEvent:
        return TaskEvent(
            task_id=task.id,
            project_id=task.project_id,
            event_type=event_type,
            revision=task.revision,
            payload=task.model_dump(mode="json", exclude_computed_fields=True),
            cursor=cursor,
        )
