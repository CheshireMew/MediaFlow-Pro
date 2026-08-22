from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mediaflow.application.events import TaskEvent
from mediaflow.application.project_workflow_service import ProjectWorkflowService
from mediaflow.application.task_service import TaskService
from mediaflow.application.workflow_models import WorkflowUpdate
from mediaflow.domain.tasks import Task


class EditorProjectTaskWorkflowCommands:
    _tasks: TaskService
    _workflows: ProjectWorkflowService

    if TYPE_CHECKING:

        def _require_writable(self) -> None: ...

    def subscribe_task_events(
        self,
        callback: Callable[[TaskEvent], None],
        *,
        include_snapshot: bool = True,
    ) -> int:
        return self._tasks.events.subscribe(callback, include_snapshot=include_snapshot)

    def unsubscribe_task_events(self, token: int) -> None:
        self._tasks.events.unsubscribe(token)

    def list_tasks(self) -> list[Task]:
        return self._tasks.list()

    def task_snapshot(self) -> tuple[list[Task], int]:
        return self._tasks.snapshot()

    def task_events_after(self, cursor: int, *, limit: int = 500) -> list[TaskEvent]:
        return self._tasks.events_after(cursor, limit=limit)

    def get_task(self, task_id: str) -> Task:
        return self._tasks.get(task_id)

    def wait_for_task(self, task_id: str, timeout: float | None = None) -> Task:
        return self._tasks.wait(task_id, timeout)

    def resume_task(
        self,
        task_id: str,
        *,
        allow_existing: bool = False,
    ) -> Task:
        self._require_writable()
        return self._tasks.resume(
            task_id,
            allow_existing=allow_existing,
        )

    def retry_task(self, task_id: str) -> Task:
        self._require_writable()
        return self._tasks.retry(task_id)

    def pause_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.pause(task_id)

    def cancel_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.cancel(task_id)

    def pause_all_tasks(self) -> int:
        self._require_writable()
        return self._tasks.pause_all()

    def cancel_all_tasks(self) -> int:
        self._require_writable()
        return self._tasks.cancel_all()

    def delete_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.delete(task_id)

    def clear_task_history(self) -> int:
        self._require_writable()
        return self._tasks.clear_history()

    def active_workflow(self):
        return self._workflows.active_run()

    def set_workflow_mode(self, value: bool | None) -> None:
        self._workflows.set_project_mode(value)

    def begin_download_workflow(self, *args: Any, **kwargs: Any):
        return self._workflows.begin_download(*args, **kwargs)

    def attach_export_task(self, run_id: str, task_id: str) -> None:
        self._workflows.attach_export_task(run_id, task_id)

    def cancel_workflow(self, run_id: str) -> WorkflowUpdate:
        return self._workflows.cancel(run_id)

    def skip_workflow(self, run_id: str) -> WorkflowUpdate:
        return self._workflows.skip(run_id)

    def continue_workflow(self, *args: Any, **kwargs: Any) -> WorkflowUpdate:
        return self._workflows.continue_run(*args, **kwargs)

    def reconcile_workflow(self) -> None:
        self._require_writable()
        self._workflows.reconcile_interrupted()
