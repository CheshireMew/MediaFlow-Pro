from __future__ import annotations

import logging
import time
from collections.abc import Callable

from mediaflow.application.ports import TaskStore
from mediaflow.application.task_execution_types import (
    CancellationToken,
    TaskCompletion,
    TaskLeaseLost,
    TaskSettlementCommitter,
)
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.tasks import Task, TaskExecutionTraceItem

TaskPublisher = Callable[[Task, str], None]
BackgroundErrorReporter = Callable[..., None]


class TaskPersistence:
    """Owns lease-checked task writes and atomic terminal settlement."""

    def __init__(
        self,
        repository: TaskStore,
        *,
        execution_owner_id: str,
        settlement_committer: TaskSettlementCommitter | None,
        publish: TaskPublisher,
        report_background_error: BackgroundErrorReporter,
    ) -> None:
        self.repository = repository
        self.execution_owner_id = execution_owner_id
        self.settlement_committer = settlement_committer
        self.publish = publish
        self.report_background_error = report_background_error

    def owned(
        self,
        task_id: str,
        token: CancellationToken,
        *,
        event_type: str,
        update: Callable[[Task], Task],
        release_owner: bool = False,
        observe_stop: bool = True,
        publish: bool = True,
    ) -> Task:
        for _attempt in range(8):
            current = self.repository.get(task_id)
            if (
                current.status != TaskStatus.RUNNING
                or current.execution_owner_id != self.execution_owner_id
            ):
                token.request_lease_lost()
                raise TaskLeaseLost(
                    f"Task {task_id} is no longer owned by this service"
                )
            self.sync_control(current, token)
            if observe_stop:
                token.raise_if_requested()
            persisted = self.repository.update_owned(
                update(current),
                self.execution_owner_id,
                event_type=event_type,
                release_owner=release_owner,
            )
            if persisted is not None:
                if publish:
                    self.publish(persisted, event_type)
                return persisted
        token.request_lease_lost()
        raise TaskLeaseLost(
            f"Task {task_id} could not update its persisted lease state"
        )

    def stopped(
        self,
        task_id: str,
        token: CancellationToken,
        status: TaskStatus,
    ) -> None:
        def stopped_task(current: Task) -> Task:
            target = (
                TaskStatus.CANCELLED
                if status == TaskStatus.CANCELLED or current.stop_request == "cancel"
                else TaskStatus.PAUSED
            )
            return current.model_copy(
                update={
                    "status": target,
                    "progress": OperationProgress.indeterminate(target.value),
                    "error": None,
                    "execution_trace": self.finish_trace(current, "cancelled"),
                }
            )

        self.settlement(
            task_id,
            token,
            event_type="status",
            update=stopped_task,
        )

    def completion(
        self,
        task_id: str,
        token: CancellationToken,
        completion: TaskCompletion,
        *,
        project_changes: tuple[Callable[[], None], ...],
    ) -> None:
        def completed_task(current: Task) -> Task:
            return current.model_copy(
                update={
                    "status": TaskStatus.COMPLETED,
                    "progress": OperationProgress.determinate(
                        "completed",
                        completed=1,
                        total=1,
                        unit="task",
                    ),
                    "artifacts": list(completion.artifacts),
                    "outcome": completion.outcome,
                    "execution_trace": self.finish_trace(current, "success"),
                    "error": None,
                }
            )

        self.settlement(
            task_id,
            token,
            event_type="completed",
            update=completed_task,
            project_changes=project_changes,
        )

    def settlement(
        self,
        task_id: str,
        token: CancellationToken,
        *,
        event_type: str,
        update: Callable[[Task], Task],
        project_changes: tuple[Callable[[], None], ...] = (),
    ) -> None:
        def persist() -> Task:
            return self.owned(
                task_id,
                token,
                event_type=event_type,
                release_owner=True,
                observe_stop=False,
                publish=self.settlement_committer is None,
                update=update,
            )

        if self.settlement_committer is not None:
            current = self.repository.get(task_id)
            persisted = self.settlement_committer(
                current,
                persist,
                project_changes,
            )
            self.publish(persisted, event_type)
            return

        for change in project_changes:
            change()
        for attempt in range(8):
            try:
                persist()
                return
            except TaskLeaseLost:
                try:
                    current = self.repository.get(task_id)
                except Exception:
                    return
                if current.status.is_settled:
                    return
                return
            except Exception:
                try:
                    current_after_failure = self.repository.get(task_id)
                except Exception:
                    current_after_failure = None
                if (
                    current_after_failure is not None
                    and current_after_failure.status.is_settled
                ):
                    return
                if attempt == 7:
                    self.report_background_error(
                        f"settlement:{task_id}",
                        (
                            f"Task {task_id} prepared its result but its "
                            "settlement receipt could not be persisted; "
                            "lease recovery will retry the task"
                        ),
                        level=logging.ERROR,
                    )
                    raise
                time.sleep(min(0.5, 0.05 * (2**attempt)))

    def failure_or_stop(
        self,
        task_id: str,
        token: CancellationToken,
        error: Exception,
    ) -> None:
        try:
            current = self.repository.get(task_id)
            if (
                current.status != TaskStatus.RUNNING
                or current.execution_owner_id != self.execution_owner_id
            ):
                return
            if current.stop_request is not None:
                self.stopped(
                    task_id,
                    token,
                    (
                        TaskStatus.CANCELLED
                        if current.stop_request == "cancel"
                        else TaskStatus.PAUSED
                    ),
                )
                return
            self.settlement(
                task_id,
                token,
                event_type="failed",
                update=lambda owned: owned.model_copy(
                    update={
                        "status": TaskStatus.FAILED,
                        "progress": OperationProgress.indeterminate("failed"),
                        "error": str(error),
                        "execution_trace": self.finish_trace(
                            owned,
                            "failed",
                            error=str(error),
                        ),
                    }
                ),
            )
        except TaskLeaseLost:
            return

    @staticmethod
    def sync_control(task: Task, token: CancellationToken) -> None:
        if task.stop_request == "cancel":
            token.request_cancel()
        elif task.stop_request == "pause":
            token.request_pause()

    @staticmethod
    def advance_trace(
        task: Task,
        message_code: str,
    ) -> list[TaskExecutionTraceItem]:
        timestamp = now_ms()
        trace = list(task.execution_trace)
        if trace and trace[-1].status == "running" and trace[-1].step == message_code:
            return trace
        if trace and trace[-1].status == "running":
            trace[-1] = trace[-1].model_copy(
                update={
                    "status": "success",
                    "duration_ms": max(0, timestamp - trace[-1].started_at),
                }
            )
        trace.append(TaskExecutionTraceItem(step=message_code, started_at=timestamp))
        return trace

    @staticmethod
    def finish_trace(
        task: Task,
        status: str,
        *,
        error: str | None = None,
    ) -> list[TaskExecutionTraceItem]:
        timestamp = now_ms()
        trace = list(task.execution_trace)
        if not trace or trace[-1].status != "running":
            trace.append(
                TaskExecutionTraceItem(
                    step=(
                        task.progress.message_code
                        if task.progress.message_code != "running"
                        else task.kind.value
                    ),
                    started_at=task.updated_at,
                )
            )
        trace[-1] = trace[-1].model_copy(
            update={
                "status": status,
                "duration_ms": max(0, timestamp - trace[-1].started_at),
                "error": error,
            }
        )
        return trace
