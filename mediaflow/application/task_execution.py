from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from contextlib import nullcontext

from mediaflow.application.ports import TaskStore
from mediaflow.application.task_execution_types import (
    CancellationToken,
    TaskContext,
    TaskHandler,
    TaskLeaseLost,
    TaskPreparationScope,
    TaskProjectChangeCommitter,
    TaskStopped,
    task_kind_handler,
)
from mediaflow.application.task_persistence import (
    BackgroundErrorReporter,
    TaskPersistence,
    TaskPublisher,
)
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.tasks import Task

MINIMUM_RELIABLE_LEASE_DURATION_MS = 1_000
RESOURCE_INTENSIVE_TASK_KINDS = frozenset(
    {
        TaskKind.PROXY,
        TaskKind.WAVEFORM,
        TaskKind.TRANSCRIBE,
        TaskKind.DUBBING,
        TaskKind.EXPORT,
        TaskKind.WEB_RENDER,
        TaskKind.IMPORT,
    }
)


class TaskExecutionEngine:
    """Owns task handlers, worker threads, cancellation tokens and leases."""

    def __init__(
        self,
        repository: TaskStore,
        persistence: TaskPersistence,
        *,
        execution_owner_id: str,
        max_workers: int,
        lease_duration_ms: int,
        preparation_scope: TaskPreparationScope | None,
        discard_preparation: Callable[[Task], None] | None,
        project_change_committer: TaskProjectChangeCommitter | None,
        publish: TaskPublisher,
        report_background_error: BackgroundErrorReporter,
        capacity_available: Callable[[], None],
    ) -> None:
        self.repository = repository
        self.persistence = persistence
        self.execution_owner_id = execution_owner_id
        self.lease_duration_ms = max(
            lease_duration_ms,
            MINIMUM_RELIABLE_LEASE_DURATION_MS,
        )
        self.heartbeat_interval = max(
            0.01,
            min(5.0, lease_duration_ms / 3000.0),
        )
        self.preparation_scope = preparation_scope or (lambda _task: nullcontext())
        self.discard_preparation = discard_preparation
        self.project_change_committer = project_change_committer
        self.publish = publish
        self.report_background_error = report_background_error
        self.capacity_available = capacity_available
        self.max_workers = max_workers
        self.handlers: dict[TaskKind, TaskHandler] = {}
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mediaflow-task",
        )
        self.resource_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mediaflow-heavy-task",
        )
        self._resource_task_ids: set[str] = set()
        self.tokens: dict[str, CancellationToken] = {}
        self.futures: dict[str, Future[None]] = {}
        self.lock = threading.RLock()
        self._dispatch_lock = threading.Lock()
        self.accepting = True

    def register(self, kind: TaskKind, handler: TaskHandler) -> None:
        with self.lock:
            self.require_accepting()
            if kind in self.handlers:
                value = getattr(kind, "value", kind)
                raise ValueError(f"Task handler already registered: {value}")
            self.handlers[kind] = handler

    def has_handler(self, task: Task) -> bool:
        return task.kind in self.handlers

    def require_handler(self, task: Task) -> TaskHandler:
        return task_kind_handler(self.handlers, task)

    def schedule(self, task: Task) -> bool:
        token = CancellationToken()
        with self.lock:
            self.require_accepting()
            if task.id in self.tokens:
                return False
            if len(self.tokens) >= self.max_workers:
                return False
            resource_intensive = task.kind in RESOURCE_INTENSIVE_TASK_KINDS
            if resource_intensive and self._resource_task_ids:
                return False
            self.tokens[task.id] = token
            if resource_intensive:
                self._resource_task_ids.add(task.id)
            try:
                executor = (
                    self.resource_executor
                    if task.kind in RESOURCE_INTENSIVE_TASK_KINDS
                    else self.executor
                )
                future = executor.submit(self._run, task.id, token)
            except Exception:
                if self.tokens.get(task.id) is token:
                    self.tokens.pop(task.id, None)
                    self._resource_task_ids.discard(task.id)
                raise
            self.futures[task.id] = future

            def forget(
                completed: Future[None],
                *,
                task_id: str = task.id,
            ) -> None:
                self._forget_future(task_id, token, completed)

            future.add_done_callback(forget)
            return True

    def admission_snapshot(self) -> tuple[int, tuple[TaskKind, ...], tuple[str, ...]]:
        """Only materialize durable pending tasks that can actually run."""
        with self.lock:
            if not self.accepting:
                return 0, (), ()
            kinds = tuple(
                kind for kind in self.handlers
                if not self._resource_task_ids or kind not in RESOURCE_INTENSIVE_TASK_KINDS
            )
            return self.max_workers - len(self.tokens), kinds, tuple(self.tokens)

    def dispatch_claimable(self, *, include_expired: bool) -> int:
        """Admit a bounded durable batch using the same capacity owner as schedule."""
        with self._dispatch_lock:
            scheduled = 0
            # Completions can free slots during a pass; cap total work per wake.
            for _ in range(self.max_workers):
                capacity, kinds, excluded = self.admission_snapshot()
                if capacity <= 0 or not kinds:
                    break
                tasks = self.repository.list_claimable(
                    now_ms(), limit=min(capacity, self.max_workers - scheduled),
                    kinds=kinds, exclude_ids=excluded, include_expired=include_expired,
                )
                if not tasks:
                    break
                accepted = 0
                for task in tasks:
                    with self.lock:
                        if not self.accepting:
                            return scheduled
                        if self.schedule(task):
                            accepted += 1
                scheduled += accepted
                if not accepted or scheduled >= self.max_workers:
                    break
            return scheduled

    def request_pause(self, task_id: str) -> None:
        with self.lock:
            token = self.tokens.get(task_id)
        if token is not None:
            token.request_pause()

    def request_cancel(self, task_id: str) -> None:
        with self.lock:
            token = self.tokens.get(task_id)
        if token is not None:
            token.request_cancel()

    def begin_shutdown(
        self,
    ) -> tuple[tuple[tuple[str, CancellationToken], ...], dict[str, Future[None]]]:
        with self.lock:
            self.accepting = False
            return tuple(self.tokens.items()), dict(self.futures)

    def wait_for_shutdown(
        self,
        futures: dict[str, Future[None]],
        *,
        timeout: float | None,
    ) -> tuple[str, ...]:
        unfinished = set(futures.values())
        if unfinished:
            _done, unfinished = wait_futures(unfinished, timeout=timeout)
        if unfinished:
            return tuple(
                task_id
                for task_id, future in futures.items()
                if future in unfinished
            )
        return ()

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.resource_executor.shutdown(wait=True, cancel_futures=True)

    def require_accepting(self) -> None:
        if not self.accepting:
            raise RuntimeError("Task service is shutting down")

    def _run(self, task_id: str, token: CancellationToken) -> None:
        context: TaskContext | None = None
        result_committed = False
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
            self.publish(running, "status")
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
                updated = self.persistence.owned(
                    task_id,
                    token,
                    event_type="progress",
                    update=lambda current: current.model_copy(
                        update={
                            "progress": progress,
                            "execution_trace": self.persistence.advance_trace(
                                current,
                                progress.message_code,
                            ),
                        }
                    ),
                )
                last_progress = updated.progress
                last_persisted_at = monotonic_now

            self.persistence.sync_control(running, token)
            context = TaskContext(
                task=running,
                project_dir=self.repository.project_dir,
                cancellation=token,
                report=report,
                recovered=recovered,
            )
            project_change_committer = self.project_change_committer
            if project_change_committer is not None:
                context._project_change_committer = (
                    lambda change: project_change_committer(running, change)
                )
            try:
                token.raise_if_requested()
                running.command.validate_for_execution()
                handler = self.require_handler(running)
                with self.preparation_scope(running):
                    completion = handler(context)
            except TaskLeaseLost:
                return
            except TaskStopped as stopped:
                context.discard_prepared_result()
                self.persistence.stopped(task_id, token, stopped.status)
            except Exception as error:
                context.discard_prepared_result()
                self.persistence.failure_or_stop(task_id, token, error)
            else:
                try:
                    persisted = self.persistence.completion(
                        task_id,
                        token,
                        completion,
                        project_changes=context.project_changes(),
                    )
                    result_committed = persisted is not None and persisted.status == TaskStatus.COMPLETED
                except TaskLeaseLost:
                    return
                except Exception as error:
                    context.discard_prepared_result()
                    self.persistence.failure_or_stop(task_id, token, error)
        finally:
            if context is not None and not result_committed:
                try:
                    if self.discard_preparation is not None:
                        self.discard_preparation(context.task)
                    context.discard_prepared_result()
                except Exception:
                    self.report_background_error(
                        f"discard:{task_id}", f"Task {task_id} could not archive its uncommitted result",
                    )
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join()

    def _heartbeat_loop(
        self,
        task_id: str,
        token: CancellationToken,
        stop: threading.Event,
        initial_expiry: int | None,
    ) -> None:
        lease_expires_at = initial_expiry or 0
        while not stop.wait(self.heartbeat_interval):
            try:
                renewed = self.repository.renew_lease(
                    task_id,
                    self.execution_owner_id,
                    self.lease_duration_ms,
                )
            except Exception:
                if now_ms() >= lease_expires_at:
                    self.report_background_error(
                        f"heartbeat-expired:{task_id}",
                        (
                            f"Task heartbeat for {task_id} could not renew "
                            "before its lease expired"
                        ),
                        level=40,
                    )
                    token.request_lease_lost()
                    return
                self.report_background_error(
                    f"heartbeat:{task_id}",
                    f"Task heartbeat for {task_id} could not renew its lease",
                )
                continue
            if renewed is None:
                token.request_lease_lost()
                return
            lease_expires_at = renewed.lease_expires_at or lease_expires_at
            self.persistence.sync_control(renewed, token)

    def _forget_future(
        self, task_id: str, token: CancellationToken, completed: Future[None],
    ) -> None:
        with self.lock:
            if self.futures.get(task_id) is completed:
                self.futures.pop(task_id, None)
                if self.tokens.get(task_id) is token:
                    self.tokens.pop(task_id, None)
                    self._resource_task_ids.discard(task_id)
        self.capacity_available()
