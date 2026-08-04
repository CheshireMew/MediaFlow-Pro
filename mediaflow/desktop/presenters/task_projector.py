from __future__ import annotations

from mediaflow.desktop.presentation_catalogs import (
    export_recovery_configuration_label,
    task_message_label,
    task_status_label,
    task_title,
    transcription_configuration_label,
)
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.task_commands import TranscribeSequenceCommand
from mediaflow.domain.tasks import ExportTaskOutcome

from .base import Projector


class TaskProjector(Projector):
    def refresh_tasks(self) -> None:
        project = self._session.binding.current
        if project is None:
            self._session.models.tasks.set_items([])
            self._session.events.tasksChanged.emit()
            return
        tasks = sorted(
            self._session.task_state.items.values(),
            key=lambda task: (task.created_at, task.id),
        )
        pending_ids = [task.id for task in tasks if task.status == TaskStatus.PENDING]
        queue_positions = {task_id: position for position, task_id in enumerate(pending_ids, start=1)}
        self._session.models.tasks.set_items(
            [
                {
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
                        task.outcome.hardware_fallback_used
                        if isinstance(task.outcome, ExportTaskOutcome)
                        else False
                    ),
                    "commandType": task.command.command_type,
                    "kind": task.kind.value,
                    "status": task.status.value,
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
                    "queuePosition": queue_positions.get(task.id, 0),
                    "inputAssetIds": list(task.input_asset_ids),
                    "contextId": self._task_context_id(task.command),
                    "error": task.error or "",
                    "artifacts": [
                        item.display_path(project.project_dir)
                        for item in task.artifacts
                    ],
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
                for task in reversed(tasks)
            ]
        )
        self._session.events.tasksChanged.emit()

    @staticmethod
    def _task_context_id(command: object) -> str:
        for attribute in ("document_id", "sequence_id", "asset_id"):
            value = getattr(command, attribute, None)
            if value:
                return str(value)
        return ""

    def refresh_download_entries(self) -> None:
        self._session.models.download_entries.set_items(
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
                    "selected": entry.index in self._session.download_state.selected_entries,
                }
                for entry in (
                    self._session.download_state.plan.entries if self._session.download_state.plan else []
                )
            ]
        )
