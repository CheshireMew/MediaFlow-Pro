from __future__ import annotations

import builtins
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from mediaflow.application.events import TaskEvent, TaskEventBus
from mediaflow.application.ports import TaskStore
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.task_commands import TaskCommand
from mediaflow.domain.tasks import (
    ArtifactReference,
    Task,
    TaskExecutionTraceItem,
    TaskOutcome,
)

logger = logging.getLogger(__name__)
BACKGROUND_ERROR_LOG_INTERVAL_SECONDS = 30.0
MINIMUM_RELIABLE_LEASE_DURATION_MS = 1_000


class TaskStopped(RuntimeError):
    def __init__(self, status: TaskStatus):
        super().__init__(status.value)
        self.status = status


class TaskLeaseLost(RuntimeError):
    pass


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


class CancellationToken:
    def __init__(self):
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
        """Register project state that must join the terminal task command."""

        if self._project_change_committed:
            raise RuntimeError(
                "A task cannot mix prerequisite and terminal project commands"
            )
        self._project_changes.append(change)

    def project_changes(self) -> tuple[Callable[[], None], ...]:
        return tuple(self._project_changes)

    def commit_project_change(self, change: Callable[[], None]) -> None:
        """Submit one prerequisite project command before external task work."""

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
        # A sub-second persisted lease can expire while Windows is merely
        # delaying one Python heartbeat thread under CPU or I/O pressure.
        # Keep the requested interval for responsive heartbeats, but never
        # publish a takeover window shorter than the scheduler-safe floor.
        self.lease_duration_ms = max(
            lease_duration_ms,
            MINIMUM_RELIABLE_LEASE_DURATION_MS,
        )
        self._heartbeat_interval = max(
            0.01,
            min(5.0, lease_duration_ms / 3000.0),
        )
        self._recovery_poll_interval = recovery_poll_interval
        self._recover_expired = recover_expired and not repository.read_only
        self._handlers: dict[TaskKind, TaskHandler] = {}
        self._preparation_scope = preparation_scope or (
            lambda _task: nullcontext()
        )
        self._settlement_committer = settlement_committer
        self._project_change_committer = project_change_committer
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mediaflow-task",
        )
        self._tokens: dict[str, CancellationToken] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.RLock()
        self._stopping = False
        self._stopped = False
        self._maintenance_stop = threading.Event()
        self._maintenance_thread: threading.Thread | None = None
        self._background_error_log_times: dict[str, float] = {}
        self.events = TaskEventBus(self._snapshot_events)
        if self._recover_expired:
            self._maintenance_thread = threading.Thread(
                target=self._maintenance_loop,
                name="mediaflow-task-recovery",
                daemon=True,
            )
            self._maintenance_thread.start()

    def register(self, kind: TaskKind, handler: TaskHandler) -> None:
        with self._lock:
            self._require_accepting_tasks()
            if kind in self._handlers:
                raise ValueError(
                    f"Task handler already registered: {kind.value}"
                )
            self._handlers[kind] = handler

    def recover_claimable(self) -> int:
        if not self._recover_expired:
            return 0
        with self._lock:
            if self._stopping:
                return 0
        scheduled = 0
        for task in self.repository.list_claimable(now_ms()):
            if task.kind not in self._handlers:
                continue
            if self._schedule(task):
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
        self._require_writable()
        command_sequence_id = getattr(
            command,
            "sequence_id",
            None,
        )
        if isinstance(command_sequence_id, str):
            if (
                sequence_id is not None
                and sequence_id != command_sequence_id
            ):
                raise ValueError(
                    "Task sequence must match its command sequence"
                )
            sequence_id = command_sequence_id
        with self._lock:
            self._require_accepting_tasks()
            command.validate_for_execution()
            kind = command.task_kind
            if kind not in self._handlers:
                raise KeyError(
                    f"No task handler registered for {kind.value}"
                )
            candidate = Task(
                project_id=project_id,
                sequence_id=sequence_id,
                idempotency_key=idempotency_key,
                command=command,
                input_asset_ids=input_asset_ids or [],
            )
            task = self.repository.create(
                candidate,
                event_type="created",
            )
            if task.id == candidate.id:
                self._publish(task, "created")
            if task.status.is_in_flight:
                self._schedule(task)
            return task

    def resume(
        self,
        task_id: str,
        *,
        allow_existing: bool = False,
    ) -> Task:
        self._require_writable()
        with self._lock:
            self._require_accepting_tasks()
            task = self.repository.get(task_id)
            if task.kind not in self._handlers:
                raise KeyError(
                    f"No task handler registered for {task.kind.value}"
                )
            resumed = self.repository.queue_paused(task_id)
            if resumed is None:
                current = self.repository.get(task_id)
                if allow_existing and (
                    current.status.is_active
                    or current.status.is_consumable
                ):
                    if current.status.is_in_flight:
                        self._schedule(current)
                    return current
                raise ValueError("Only paused tasks can be resumed")
            self._publish(resumed, "status")
            self._schedule(resumed)
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
            self._publish(
                requested,
                "control" if requested.status == TaskStatus.RUNNING else "status",
            )
        with self._lock:
            token = self._tokens.get(task_id)
        if token is not None:
            token.request_pause()

    def cancel(self, task_id: str) -> None:
        self._require_writable()
        requested = self.repository.request_stop(task_id, "cancel")
        if requested is not None:
            self._publish(
                requested,
                "control" if requested.status == TaskStatus.RUNNING else "status",
            )
        else:
            current = self.repository.get(task_id)
            if not current.status.is_active:
                raise ValueError("Only active tasks can be cancelled")
        with self._lock:
            token = self._tokens.get(task_id)
        if token is not None:
            token.request_cancel()

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
            self._publish(
                requested,
                "control" if requested.status == TaskStatus.RUNNING else "status",
            )
            with self._lock:
                token = self._tokens.get(task.id)
            if token is not None:
                token.request_cancel()
            count += 1
        return count

    def delete(self, task_id: str) -> None:
        self._require_writable()
        task = self.repository.get(task_id)
        if not task.status.is_terminal:
            raise ValueError("Only completed, failed, or cancelled tasks can be removed")
        self.repository.delete(task_id, event_type="deleted")
        with self._lock:
            self._futures.pop(task_id, None)
        self._publish(task, "deleted")

    def clear_history(self) -> int:
        self._require_writable()
        tasks = self.repository.delete_terminal()
        with self._lock:
            for task in tasks:
                self._futures.pop(task.id, None)
        for task in tasks:
            self._publish(task, "deleted")
        return len(tasks)

    def wait(self, task_id: str, timeout: float | None = None) -> Task:
        if timeout is not None and timeout < 0:
            raise ValueError("Task wait timeout cannot be negative")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            task = self.repository.get(task_id)
            if task.status.is_settled:
                return task
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Task {task_id} did not finish within {timeout} seconds"
                    )
                time.sleep(min(0.05, remaining))
            else:
                time.sleep(0.05)

    def get(self, task_id: str) -> Task:
        return self.repository.get(task_id)

    def list(self) -> builtins.list[Task]:
        return self.repository.list()

    def snapshot(self) -> tuple[builtins.list[Task], int]:
        return self.repository.snapshot()

    def events_after(self, cursor: int, *, limit: int = 500) -> builtins.list[TaskEvent]:
        return self.repository.events_after(cursor, limit=limit)

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
            owned = tuple(self._tokens.items())
            futures = dict(self._futures)
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
        unfinished_futures = set(futures.values())
        if unfinished_futures:
            _done, unfinished_futures = wait_futures(
                unfinished_futures,
                timeout=self._remaining_timeout(deadline),
            )
        if maintenance_running or unfinished_futures:
            unfinished_task_ids = tuple(
                task_id
                for task_id, future in futures.items()
                if future in unfinished_futures
            )
            raise TaskShutdownTimeout(
                unfinished_task_ids,
                maintenance_running=maintenance_running,
            )
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._stopped = True

    def _schedule(self, task: Task) -> bool:
        token = CancellationToken()
        with self._lock:
            self._require_accepting_tasks()
            if task.id in self._tokens:
                return False
            self._tokens[task.id] = token
            try:
                future = self._executor.submit(
                    self._run,
                    task.id,
                    token,
                )
            except Exception:
                if self._tokens.get(task.id) is token:
                    self._tokens.pop(task.id, None)
                raise
            self._futures[task.id] = future

            def forget(
                completed: Future[None],
                *,
                task_id: str = task.id,
            ) -> None:
                self._forget_future(task_id, completed)

            future.add_done_callback(forget)
            return True

    def _run(self, task_id: str, token: CancellationToken) -> None:
        heartbeat_stop: threading.Event | None = None
        heartbeat_thread: threading.Thread | None = None
        try:
            claim = self.repository.claim(
                task_id,
                self.execution_owner_id,
                self.lease_duration_ms,
            )
            if claim is None:
                return
            running, recovered = claim
            self._publish(running, "status")
            heartbeat_stop = threading.Event()
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(task_id, token, heartbeat_stop, running.lease_expires_at),
                name=f"mediaflow-task-heartbeat-{task_id[:8]}",
                daemon=True,
            )
            heartbeat_thread.start()

            last_progress = running.progress
            last_persisted_at = time.monotonic()

            def report(progress: OperationProgress) -> None:
                nonlocal last_progress, last_persisted_at
                token.raise_if_requested()
                monotonic_now = time.monotonic()
                same_phase = (
                    progress.message_code == last_progress.message_code
                    and progress.mode == last_progress.mode
                    and progress.unit == last_progress.unit
                    and progress.total == last_progress.total
                )
                previous_percent = last_progress.percent
                current_percent = progress.percent
                if (
                    same_phase
                    and monotonic_now - last_persisted_at < 0.1
                    and (
                        progress.mode == "indeterminate"
                        or (
                            previous_percent is not None
                            and current_percent is not None
                            and current_percent < 100.0
                            and abs(current_percent - previous_percent) < 1.0
                        )
                    )
                ):
                    return
                updated = self._persist_owned(
                    task_id,
                    token,
                    event_type="progress",
                    update=lambda current: current.model_copy(
                        update={
                            "progress": progress,
                            "execution_trace": self._advance_trace(
                                current,
                                progress.message_code,
                            ),
                        }
                    ),
                )
                last_progress = updated.progress
                last_persisted_at = monotonic_now

            self._sync_control(running, token)
            context = TaskContext(
                task=running,
                project_dir=self.repository.project_dir,
                cancellation=token,
                report=report,
                recovered=recovered,
            )
            project_change_committer = self._project_change_committer
            if project_change_committer is not None:
                context._project_change_committer = (
                    lambda change: project_change_committer(
                        running,
                        change,
                    )
                )
            try:
                token.raise_if_requested()
                running.command.validate_for_execution()
                handler = self._handlers[running.kind]
                with self._preparation_scope(running):
                    completion = handler(context)
            except TaskLeaseLost:
                return
            except TaskStopped as stopped:
                self._persist_stopped(task_id, token, stopped.status)
            except Exception as error:
                self._persist_failure_or_stop(task_id, token, error)
            else:
                try:
                    self._persist_completion(
                        task_id,
                        token,
                        completion,
                        project_changes=context.project_changes(),
                    )
                except TaskLeaseLost:
                    return
                except Exception as error:
                    self._persist_failure_or_stop(task_id, token, error)
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join()
            with self._lock:
                if self._tokens.get(task_id) is token:
                    self._tokens.pop(task_id, None)

    def _persist_owned(
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
            self._sync_control(current, token)
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
                    self._publish(persisted, event_type)
                return persisted
        token.request_lease_lost()
        raise TaskLeaseLost(
            f"Task {task_id} could not update its persisted lease state"
        )

    def _persist_stopped(
        self,
        task_id: str,
        token: CancellationToken,
        status: TaskStatus,
    ) -> None:
        def stopped_task(current: Task) -> Task:
            target = (
                TaskStatus.CANCELLED
                if (
                    status == TaskStatus.CANCELLED
                    or current.stop_request == "cancel"
                )
                else TaskStatus.PAUSED
            )
            return current.model_copy(
                update={
                    "status": target,
                    "progress": OperationProgress.indeterminate(
                        target.value
                    ),
                    "error": None,
                    "execution_trace": self._finish_trace(
                        current,
                        "cancelled",
                    ),
                }
            )

        self._persist_settlement(
            task_id,
            token,
            event_type="status",
            update=stopped_task,
        )

    def _persist_completion(
        self,
        task_id: str,
        token: CancellationToken,
        completion: TaskCompletion,
        *,
        project_changes: tuple[Callable[[], None], ...],
    ) -> None:
        """Persist success without ever relabeling published work as failed."""

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
                    "execution_trace": self._finish_trace(
                        current,
                        "success",
                    ),
                    "error": None,
                }
            )

        self._persist_settlement(
            task_id,
            token,
            event_type="completed",
            update=completed_task,
            project_changes=project_changes,
        )

    def _persist_settlement(
        self,
        task_id: str,
        token: CancellationToken,
        *,
        event_type: str,
        update: Callable[[Task], Task],
        project_changes: tuple[Callable[[], None], ...] = (),
    ) -> None:
        """Commit one settled task and its project result as one boundary."""

        def persist() -> Task:
            return self._persist_owned(
                task_id,
                token,
                event_type=event_type,
                release_owner=True,
                observe_stop=False,
                publish=self._settlement_committer is None,
                update=update,
            )

        if self._settlement_committer is not None:
            current = self.repository.get(task_id)
            persisted = self._settlement_committer(
                current,
                persist,
                project_changes,
            )
            self._publish(persisted, event_type)
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
                    self._log_background_error(
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

    def _persist_failure_or_stop(
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
                self._persist_stopped(
                    task_id,
                    token,
                    (
                        TaskStatus.CANCELLED
                        if current.stop_request == "cancel"
                        else TaskStatus.PAUSED
                    ),
                )
                return
            self._persist_settlement(
                task_id,
                token,
                event_type="failed",
                update=lambda owned: owned.model_copy(
                    update={
                        "status": TaskStatus.FAILED,
                        "progress": OperationProgress.indeterminate("failed"),
                        "error": str(error),
                        "execution_trace": self._finish_trace(
                            owned,
                            "failed",
                            error=str(error),
                        ),
                    }
                ),
            )
        except TaskLeaseLost:
            return

    def _heartbeat_loop(
        self,
        task_id: str,
        token: CancellationToken,
        stop: threading.Event,
        initial_expiry: int | None,
    ) -> None:
        lease_expires_at = initial_expiry or 0
        while not stop.wait(self._heartbeat_interval):
            try:
                renewed = self.repository.renew_lease(
                    task_id,
                    self.execution_owner_id,
                    self.lease_duration_ms,
                )
            except Exception:
                if now_ms() >= lease_expires_at:
                    self._log_background_error(
                        f"heartbeat-expired:{task_id}",
                        (
                            f"Task heartbeat for {task_id} could not renew "
                            "before its lease expired"
                        ),
                        level=logging.ERROR,
                    )
                    token.request_lease_lost()
                    return
                self._log_background_error(
                    f"heartbeat:{task_id}",
                    f"Task heartbeat for {task_id} could not renew its lease",
                )
                continue
            if renewed is None:
                token.request_lease_lost()
                return
            lease_expires_at = renewed.lease_expires_at or lease_expires_at
            self._sync_control(renewed, token)

    @staticmethod
    def _sync_control(task: Task, token: CancellationToken) -> None:
        if task.stop_request == "cancel":
            token.request_cancel()
        elif task.stop_request == "pause":
            token.request_pause()

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

    def _require_writable(self) -> None:
        if self.repository.read_only:
            raise PermissionError("Project task service is read-only")

    def _require_accepting_tasks(self) -> None:
        if self._stopping:
            raise RuntimeError("Task service is shutting down")

    @staticmethod
    def _remaining_timeout(
        deadline: float | None,
    ) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    def _forget_future(self, task_id: str, completed: Future[None]) -> None:
        with self._lock:
            if self._futures.get(task_id) is completed:
                self._futures.pop(task_id, None)
            self._background_error_log_times.pop(
                f"heartbeat:{task_id}",
                None,
            )
            self._background_error_log_times.pop(
                f"heartbeat-expired:{task_id}",
                None,
            )
            self._background_error_log_times.pop(
                f"completion:{task_id}",
                None,
            )

    @staticmethod
    def _advance_trace(
        task: Task,
        message_code: str,
    ) -> builtins.list[TaskExecutionTraceItem]:
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
    def _finish_trace(
        task: Task,
        status: str,
        *,
        error: str | None = None,
    ) -> builtins.list[TaskExecutionTraceItem]:
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
