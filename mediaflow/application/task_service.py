from __future__ import annotations

import builtins
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mediaflow.application.events import TaskEvent, TaskEventBus
from mediaflow.application.ports import TaskStore
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.task_commands import TaskCommand
from mediaflow.domain.tasks import Task, TaskExecutionTraceItem


class TaskStopped(RuntimeError):
    def __init__(self, status: TaskStatus):
        super().__init__(status.value)
        self.status = status


class CancellationToken:
    def __init__(self):
        self._lock = threading.Lock()
        self._requested: TaskStatus | None = None

    def request_pause(self) -> None:
        with self._lock:
            self._requested = TaskStatus.PAUSED

    def request_cancel(self) -> None:
        with self._lock:
            self._requested = TaskStatus.CANCELLED

    def raise_if_requested(self) -> None:
        with self._lock:
            requested = self._requested
        if requested is not None:
            raise TaskStopped(requested)


ProgressReporter = Callable[[float, str], None]


@dataclass(slots=True)
class TaskContext:
    task: Task
    project_dir: Path
    cancellation: CancellationToken
    report_progress: ProgressReporter


TaskHandler = Callable[[TaskContext], list[str] | None]


class TaskService:
    def __init__(
        self,
        repository: TaskStore,
        *,
        max_workers: int = 3,
        recover_interrupted: bool = True,
    ):
        self.repository = repository
        self._handlers: dict[TaskKind, TaskHandler] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mediaflow-task")
        self._tokens: dict[str, CancellationToken] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.RLock()
        self.events = TaskEventBus(self._snapshot_events)
        if recover_interrupted:
            for task in self.repository.recover_interrupted():
                self._publish(task, "status")

    def register(self, kind: TaskKind, handler: TaskHandler) -> None:
        if kind in self._handlers:
            raise ValueError(f"Task handler already registered: {kind.value}")
        self._handlers[kind] = handler

    def start(
        self,
        *,
        project_id: str,
        command: TaskCommand,
        sequence_id: str | None = None,
        input_asset_ids: builtins.list[str] | None = None,
    ) -> Task:
        kind = command.task_kind
        if kind not in self._handlers:
            raise KeyError(f"No task handler registered for {kind.value}")
        task = self.repository.create(
            Task(
                project_id=project_id,
                sequence_id=sequence_id,
                command=command,
                input_asset_ids=input_asset_ids or [],
            )
        )
        self._publish(task, "created")
        self._schedule(task)
        return task

    def resume(self, task_id: str) -> Task:
        task = self.repository.get(task_id)
        if task.status != TaskStatus.PAUSED:
            raise ValueError("Only paused tasks can be resumed")
        if task.kind not in self._handlers:
            raise KeyError(f"No task handler registered for {task.kind.value}")
        resumed = self.repository.save(
            task.model_copy(
                update={
                    "status": TaskStatus.PENDING,
                    "message_code": "queued",
                    "error": None,
                }
            )
        )
        self._publish(resumed, "status")
        self._schedule(resumed)
        return resumed

    def retry(self, task_id: str) -> Task:
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
        with self._lock:
            token = self._tokens.get(task_id)
        if token is None:
            raise KeyError(task_id)
        token.request_pause()

    def cancel(self, task_id: str) -> None:
        with self._lock:
            token = self._tokens.get(task_id)
        if token is None:
            task = self.repository.get(task_id)
            if task.status in {TaskStatus.PENDING, TaskStatus.PAUSED}:
                cancelled = self.repository.save(
                    task.model_copy(update={"status": TaskStatus.CANCELLED, "message_code": "cancelled"})
                )
                self._publish(cancelled, "status")
                return
            raise KeyError(task_id)
        token.request_cancel()

    def pause_all(self) -> int:
        count = 0
        for task in self.repository.list():
            if task.status.is_in_flight:
                self.pause(task.id)
                count += 1
        return count

    def cancel_all(self) -> int:
        count = 0
        for task in self.repository.list():
            if task.status.is_active:
                self.cancel(task.id)
                count += 1
        return count

    def delete(self, task_id: str) -> None:
        task = self.repository.get(task_id)
        if not task.status.is_terminal:
            raise ValueError("Only completed, failed, or cancelled tasks can be removed")
        self.repository.delete(task_id)
        with self._lock:
            self._futures.pop(task_id, None)
        self._publish(task, "deleted")

    def clear_history(self) -> int:
        tasks = self.repository.delete_terminal()
        with self._lock:
            for task in tasks:
                self._futures.pop(task.id, None)
        for task in tasks:
            self._publish(task, "deleted")
        return len(tasks)

    def wait(self, task_id: str, timeout: float | None = None) -> Task:
        with self._lock:
            future = self._futures.get(task_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.repository.get(task_id)

    def get(self, task_id: str) -> Task:
        return self.repository.get(task_id)

    def list(self) -> builtins.list[Task]:
        return self.repository.list()

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            tokens = tuple(self._tokens.values())
        for token in tokens:
            token.request_pause()
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _schedule(self, task: Task) -> None:
        token = CancellationToken()
        with self._lock:
            self._tokens[task.id] = token
        try:
            future = self._executor.submit(self._run, task.id, token)
        except Exception:
            with self._lock:
                if self._tokens.get(task.id) is token:
                    self._tokens.pop(task.id, None)
            raise
        with self._lock:
            self._futures[task.id] = future

        def forget(completed: Future[None], *, task_id: str = task.id) -> None:
            self._forget_future(task_id, completed)

        future.add_done_callback(forget)

    def _run(self, task_id: str, token: CancellationToken) -> None:
        task = self.repository.get(task_id)
        running = self.repository.save(
            task.model_copy(update={"status": TaskStatus.RUNNING, "message_code": "running", "error": None})
        )
        self._publish(running, "status")

        last_progress = running.progress
        last_message = running.message_code
        last_persisted_at = time.monotonic()

        def report(progress: float, message_code: str) -> None:
            nonlocal last_progress, last_message, last_persisted_at
            token.raise_if_requested()
            normalized = max(0.0, min(100.0, float(progress)))
            now = time.monotonic()
            if (
                message_code == last_message
                and normalized < 100.0
                and abs(normalized - last_progress) < 1.0
                and now - last_persisted_at < 0.1
            ):
                return
            current = self.repository.get(task_id)
            trace = self._advance_trace(current, message_code)
            updated = self.repository.save(
                current.model_copy(
                    update={
                        "progress": normalized,
                        "message_code": message_code,
                        "execution_trace": trace,
                    }
                )
            )
            self._publish(updated, "progress")
            last_progress = normalized
            last_message = message_code
            last_persisted_at = now

        context = TaskContext(
            task=running,
            project_dir=self.repository.project_dir,
            cancellation=token,
            report_progress=report,
        )
        try:
            token.raise_if_requested()
            handler = self._handlers[running.kind]
            artifacts = handler(context) or []
            token.raise_if_requested()
            current = self.repository.get(task_id)
            completed = self.repository.save(
                current.model_copy(
                    update={
                        "status": TaskStatus.COMPLETED,
                        "progress": 100.0,
                        "message_code": "completed",
                        "artifacts": artifacts,
                        "execution_trace": self._finish_trace(current, "success"),
                        "error": None,
                    }
                )
            )
            self._publish(completed, "completed")
        except TaskStopped as stopped:
            current = self.repository.get(task_id)
            stopped_task = self.repository.save(
                current.model_copy(
                    update={
                        "status": stopped.status,
                        "message_code": stopped.status.value,
                        "error": None,
                        "execution_trace": self._finish_trace(current, "cancelled"),
                    }
                )
            )
            self._publish(stopped_task, "status")
        except Exception as error:
            current = self.repository.get(task_id)
            failed = self.repository.save(
                current.model_copy(
                    update={
                        "status": TaskStatus.FAILED,
                        "message_code": "failed",
                        "error": str(error),
                        "execution_trace": self._finish_trace(
                            current,
                            "failed",
                            error=str(error),
                        ),
                    }
                )
            )
            self._publish(failed, "failed")
        finally:
            with self._lock:
                if self._tokens.get(task_id) is token:
                    self._tokens.pop(task_id, None)

    def _forget_future(self, task_id: str, completed: Future[None]) -> None:
        with self._lock:
            if self._futures.get(task_id) is completed:
                self._futures.pop(task_id, None)

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
                    step=task.message_code if task.message_code != "running" else task.kind.value,
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
        return [self._event(task, "snapshot") for task in self.repository.list()]

    def _publish(self, task: Task, event_type: str) -> None:
        self.events.publish(self._event(task, event_type))

    @staticmethod
    def _event(task: Task, event_type: str) -> TaskEvent:
        return TaskEvent(
            task_id=task.id,
            project_id=task.project_id,
            event_type=event_type,
            revision=task.revision,
            payload=task.model_dump(mode="json", exclude_computed_fields=True),
        )
