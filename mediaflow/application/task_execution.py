from __future__ import annotations

import threading
import time
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
from mediaflow.domain.enums import TaskKind
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.tasks import Task

MINIMUM_RELIABLE_LEASE_DURATION_MS = 1_000


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
        project_change_committer: TaskProjectChangeCommitter | None,
        publish: TaskPublisher,
        report_background_error: BackgroundErrorReporter,
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
        self.project_change_committer = project_change_committer
        self.publish = publish
        self.report_background_error = report_background_error
        self.handlers: dict[TaskKind, TaskHandler] = {}
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mediaflow-task",
        )
        self.tokens: dict[str, CancellationToken] = {}
        self.futures: dict[str, Future[None]] = {}
        self.lock = threading.RLock()
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
            self.tokens[task.id] = token
            try:
                future = self.executor.submit(self._run, task.id, token)
            except Exception:
                if self.tokens.get(task.id) is token:
                    self.tokens.pop(task.id, None)
                raise
            self.futures[task.id] = future

            def forget(
                completed: Future[None],
                *,
                task_id: str = task.id,
            ) -> None:
                self._forget_future(task_id, completed)

            future.add_done_callback(forget)
            return True

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

    def forget_task(self, task_id: str) -> None:
        with self.lock:
            self.futures.pop(task_id, None)

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

    def require_accepting(self) -> None:
        if not self.accepting:
            raise RuntimeError("Task service is shutting down")

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
                self.persistence.stopped(task_id, token, stopped.status)
            except Exception as error:
                self.persistence.failure_or_stop(task_id, token, error)
            else:
                try:
                    self.persistence.completion(
                        task_id,
                        token,
                        completion,
                        project_changes=context.project_changes(),
                    )
                except TaskLeaseLost:
                    return
                except Exception as error:
                    self.persistence.failure_or_stop(task_id, token, error)
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join()
            with self.lock:
                if self.tokens.get(task_id) is token:
                    self.tokens.pop(task_id, None)

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

    def _forget_future(self, task_id: str, completed: Future[None]) -> None:
        with self.lock:
            if self.futures.get(task_id) is completed:
                self.futures.pop(task_id, None)
