from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mediaflow.application.events import TaskEvent, TaskEventBus
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.models import Task
from mediaflow.infrastructure.task_repository import TaskRepository


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
    def __init__(self, repository: TaskRepository, *, max_workers: int = 3):
        self.repository = repository
        self._handlers: dict[TaskKind, TaskHandler] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mediaflow-task")
        self._tokens: dict[str, CancellationToken] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.RLock()
        self.events = TaskEventBus(self._snapshot_events)
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
        kind: TaskKind,
        name: str,
        sequence_id: str | None = None,
        input_asset_ids: list[str] | None = None,
        parameters: dict | None = None,
    ) -> Task:
        if kind not in self._handlers:
            raise KeyError(f"No task handler registered for {kind.value}")
        task = self.repository.create(
            Task(
                project_id=project_id,
                sequence_id=sequence_id,
                kind=kind,
                name=name,
                input_asset_ids=input_asset_ids or [],
                parameters=parameters or {},
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

    def wait(self, task_id: str, timeout: float | None = None) -> Task:
        with self._lock:
            future = self._futures.get(task_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.repository.get(task_id)

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
            self._futures[task.id] = self._executor.submit(self._run, task.id, token)

    def _run(self, task_id: str, token: CancellationToken) -> None:
        task = self.repository.get(task_id)
        running = self.repository.save(
            task.model_copy(update={"status": TaskStatus.RUNNING, "message_code": "running", "error": None})
        )
        self._publish(running, "status")

        def report(progress: float, message_code: str) -> None:
            token.raise_if_requested()
            current = self.repository.get(task_id)
            updated = self.repository.save(
                current.model_copy(
                    update={
                        "progress": max(0.0, min(100.0, float(progress))),
                        "message_code": message_code,
                    }
                )
            )
            self._publish(updated, "progress")

        context = TaskContext(
            task=running,
            project_dir=self.repository.project_dir,
            cancellation=token,
            report_progress=report,
        )
        try:
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
                    }
                )
            )
            self._publish(failed, "failed")
        finally:
            with self._lock:
                self._tokens.pop(task_id, None)

    def _snapshot_events(self) -> list[TaskEvent]:
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
            payload=task.model_dump(mode="json"),
        )
