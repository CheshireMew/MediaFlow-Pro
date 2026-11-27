from __future__ import annotations

from mediaflow.desktop.component_contexts import TaskProjectorContext
from mediaflow.desktop.presentation_asr import transcription_configuration_label
from mediaflow.desktop.presentation_tasks import (
    export_recovery_configuration_label,
    task_message_label,
    task_status_label,
    task_title,
)
from mediaflow.desktop.session_state import DesktopProject
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.task_commands import TranscribeSequenceCommand
from mediaflow.domain.tasks import ExportTaskOutcome, Task

from .base import Projector


class TaskProjector(Projector[TaskProjectorContext]):
    def __init__(self, context: TaskProjectorContext):
        super().__init__(context)
        self._project: DesktopProject | None = None
        self._rows: dict[str, dict] = {}
        self._tokens: dict[str, tuple[int, int]] = {}

    def refresh_tasks(self, *, task_ids: list[str] | None = None) -> None:
        project = self._context.state.binding.current
        if project is not self._project:
            self._rows.clear()
            self._tokens.clear()
            self._project = project
        if project is None:
            self._context.models.tasks.set_items([])
            self._context.updates.commit(tasks=True)
            return
        current = self._context.state.tasks.items
        # Progress events keep membership, ordering and pending-queue positions.
        # Publish only the affected rows; history size must not determine UI latency.
        if task_ids and all(
            task_id in current
            and task_id in self._rows
            and self._rows[task_id]["status"] == current[task_id].status.value
            and self._rows[task_id]["createdAt"] == current[task_id].created_at
            for task_id in task_ids
        ):
            rows = [
                self._row(current[task_id], project, self._rows[task_id]["queuePosition"])
                for task_id in task_ids
            ]
            if self._context.models.tasks.update_items_by_key(rows):
                self._context.updates.commit(tasks=True)
                return
        tasks = sorted(current.values(), key=lambda task: (task.created_at, task.id))
        queue_positions = {
            task.id: position
            for position, task in enumerate(
                (task for task in tasks if task.status == TaskStatus.PENDING), start=1
            )
        }
        rows = [
            self._row(task, project, queue_positions.get(task.id, 0), reuse=True) for task in reversed(tasks)
        ]
        self._rows = {row["taskId"]: row for row in rows}
        self._tokens = {task.id: (id(task), task.revision) for task in tasks}
        self._context.models.tasks.set_items(rows)
        self._context.updates.commit(tasks=True)

    def _row(self, task: Task, project: DesktopProject, queue_position: int, *, reuse: bool = False) -> dict:
        token = (id(task), task.revision)
        cached = self._rows.get(task.id)
        if reuse and cached is not None and self._tokens.get(task.id) == token:
            if cached["queuePosition"] == queue_position:
                return cached
            row = {**cached, "queuePosition": queue_position}
        else:
            row = self._project_row(task, project, queue_position)
        self._rows[task.id] = row
        self._tokens[task.id] = token
        return row

    def _project_row(self, task: Task, project: DesktopProject, queue_position: int) -> dict:
        return {
            "taskId": task.id,
            "displayName": task_title(task),
            "configurationLabel": (
                export_recovery_configuration_label(task.outcome)
                if isinstance(task.outcome, ExportTaskOutcome)
                else (
                    transcription_configuration_label(task.command)
                    if isinstance(task.command, TranscribeSequenceCommand)
                    else ""
                )
            ),
            "encoderFallbackUsed": (
                task.outcome.hardware_fallback_used if isinstance(task.outcome, ExportTaskOutcome) else False
            ),
            "commandType": task.command.command_type,
            "kind": task.kind.value,
            "status": task.status.value,
            "executionScope": task.execution_scope,
            "statusLabel": task_status_label(task.status.value),
            "progressMode": task.progress.mode,
            "progressValue": task.progress.percent or 0.0,
            "progressCompleted": task.progress.completed or 0.0,
            "progressTotal": task.progress.total or 0.0,
            "progressUnit": task.progress.unit or "",
            "hasOverallProgress": task.progress.overall_percent is not None,
            "overallProgressValue": task.progress.overall_percent or 0.0,
            "overallProgressCompleted": task.progress.overall_completed or 0.0,
            "overallProgressTotal": task.progress.overall_total or 0.0,
            "overallProgressUnit": task.progress.overall_unit or "",
            "progressItemIndex": task.progress.item_index or 0,
            "progressItemTotal": task.progress.item_total or 0,
            "progressItemLabel": task.progress.item_label or "",
            "messageCode": task.progress.message_code,
            "messageLabel": task_message_label(task.progress.message_code),
            "queuePosition": queue_position,
            "inputAssetIds": list(task.input_asset_ids),
            "contextId": self._task_context_id(task.command),
            "error": task.error or "",
            "artifacts": [item.display_path(project.project_dir) for item in task.artifacts],
            "executionTrace": [
                {
                    "step": task_message_label(item.step),
                    "duration": item.duration_ms / 1000.0,
                    "status": item.status,
                    "error": item.error or "",
                }
                for item in task.execution_trace
            ],
            "createdAt": task.created_at,
        }

    @staticmethod
    def _task_context_id(command: object) -> str:
        for attribute in ("document_id", "session_id", "sequence_id", "asset_id"):
            value = getattr(command, attribute, None)
            if value:
                return str(value)
        return ""

    def refresh_download_entries(self) -> None:
        self._context.models.download_entries.set_items(
            [
                {
                    "entryIndex": entry.index,
                    "mediaId": entry.media_id,
                    "title": entry.title,
                    "pageUrl": entry.page_url,
                    "duration": entry.duration,
                    "uploader": entry.uploader,
                    "available": entry.available,
                    "unavailableReason": entry.unavailable_reason,
                    "selected": entry.index in self._context.state.download.selected_entries,
                }
                for entry in (
                    self._context.state.download.plan.entries if self._context.state.download.plan else []
                )
            ]
        )
