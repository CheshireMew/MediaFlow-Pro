from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from mediaflow.application.events import TaskEvent
from mediaflow.domain.enums import TaskKind
from mediaflow.domain.task_commands import (
    AnalyzeHighlightsCommand,
    AnalyzeLoudnessCommand,
    AnalyzeScenesCommand,
    AnalyzeSequenceBoundsCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    TaskCommand,
    TrackSubjectCommand,
    TranscribeSequenceCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)
from mediaflow.domain.tasks import Task

from .base import SessionCoordinator

logger = logging.getLogger(__name__)


class _TaskEventBridge(QObject):
    eventReceived = Signal(object)


class TaskOperations(SessionCoordinator):
    def __init__(self, session):
        super().__init__(session)
        self._start_lock = threading.RLock()
        self._blocked_event_cursor: int | None = None
        self._terminal_replays: dict[str, Task] = {}
        self._reported_delivery_errors: set[tuple[str, int]] = set()
        self._bridge = _TaskEventBridge(session)
        self._bridge.eventReceived.connect(self._on_event)

    def publish(self, envelope: object) -> None:
        self._bridge.eventReceived.emit(envelope)

    def reset_delivery_state(self) -> None:
        self._blocked_event_cursor = None
        self._terminal_replays.clear()
        self._reported_delivery_errors.clear()

    def reconcile_committed_results(self) -> None:
        current = self._session.binding.current
        if current is None:
            return
        candidates = dict(self._terminal_replays)
        for task in current.list_tasks():
            if task.status.is_terminal:
                candidates[task.id] = task
        for task in candidates.values():
            self._session.task_state.items[task.id] = task
            if self._apply_task_update(task):
                self._terminal_replays.pop(task.id, None)
                self._session.task_state.revisions[task.id] = task.revision
            else:
                self._terminal_replays[task.id] = task

    def start(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
    ) -> Task | None:
        try:
            return self.create(
                command,
                input_asset_ids,
                sequence_id=sequence_id,
            )
        except Exception as error:
            self._session.events.errorOccurred.emit(str(error))
            return None

    def create(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
    ) -> Task:
        self._session._require_writable()
        current = self._session.binding.current
        if current is None:
            raise RuntimeError("当前没有打开的项目")
        scope = self._active_request_scope(command)
        with self._start_lock:
            task = self._active_task_for_scope(scope)
            if task is None:
                task = current.start_task(
                    command,
                    input_asset_ids,
                    sequence_id=(
                        sequence_id
                        or self._session.binding.active_sequence_id
                    ),
                )
        self._session.task_state.items[task.id] = task
        self._session.projectors.tasks.refresh_tasks()
        return task

    def _active_task_for_scope(
        self,
        scope: tuple[str, ...] | None,
    ) -> Task | None:
        if scope is None:
            return None
        current = self._session.binding.current
        if current is None:
            return None
        observed: dict[str, Task] = dict(
            self._session.task_state.items
        )
        for task in current.list_tasks():
            observed.setdefault(task.id, task)
        return next(
            (
                task
                for task in observed.values()
                if task.status.is_active
                and self._active_request_scope(task.command)
                == scope
            ),
            None,
        )

    @staticmethod
    def _active_request_scope(
        command: TaskCommand,
    ) -> tuple[str, ...] | None:
        if isinstance(command, GenerateProxyCommand):
            return ("proxy", command.asset_id)
        if isinstance(command, GenerateWaveformCommand):
            return ("waveform", command.asset_id)
        if isinstance(command, TranscribeSequenceCommand):
            return ("transcribe", command.sequence_id)
        if isinstance(
            command,
            (TranslateDocumentCommand, TranslateSegmentsCommand),
        ):
            return (
                "translate",
                command.document_id,
                command.target_language.strip().casefold(),
            )
        if isinstance(command, AnalyzeHighlightsCommand):
            return ("highlights", command.document_id)
        if isinstance(command, AnalyzeSequenceBoundsCommand):
            return ("sequence_bounds", command.sequence_id)
        if isinstance(command, AnalyzeLoudnessCommand):
            return ("loudness", command.sequence_id)
        if isinstance(command, AnalyzeScenesCommand):
            return (
                "scenes",
                command.sequence_id,
                command.clip_id,
            )
        if isinstance(command, TrackSubjectCommand):
            return (
                "subject_tracking",
                command.sequence_id,
                command.clip_id,
            )
        return None

    @Slot(object)
    def _on_event(self, envelope: object) -> None:
        if not isinstance(envelope, tuple) or len(envelope) != 2:
            return
        generation, event = envelope
        current = self._session.binding.current
        if (
            not isinstance(generation, int)
            or generation != self._session.binding.generation
            or not isinstance(event, TaskEvent)
            or event.project_id != self._session.binding.project_id
        ):
            return
        if event.cursor:
            if event.cursor <= self._session.task_state.cursor:
                return
            if (
                self._blocked_event_cursor is not None
                and event.cursor > self._blocked_event_cursor
            ):
                return
        previous_revision = self._session.task_state.revisions.get(event.task_id, -1)
        if event.event_type == "deleted":
            self._session.task_state.items.pop(event.task_id, None)
            self._session.task_state.revisions.pop(event.task_id, None)
            if event.cursor:
                self._session.task_state.cursor = event.cursor
                if self._blocked_event_cursor == event.cursor:
                    self._blocked_event_cursor = None
            self._session.projectors.tasks.refresh_tasks()
            return
        elif event.revision <= previous_revision:
            return
        try:
            task = Task.model_validate(event.payload)
        except (TypeError, ValueError) as error:
            self._report_delivery_error(
                event.task_id,
                event.revision,
                error,
            )
            if event.cursor:
                self._blocked_event_cursor = event.cursor
            return
        self._session.task_state.items[task.id] = task
        if current is None:
            if task.status.is_terminal:
                self._session.timeline_assets.finish_import_drop(
                    task.id,
                    "",
                )
                self._terminal_replays[task.id] = task
            self._session.task_state.revisions[task.id] = (
                event.revision
            )
            if event.cursor:
                self._session.task_state.cursor = event.cursor
                if self._blocked_event_cursor == event.cursor:
                    self._blocked_event_cursor = None
            self._session.projectors.tasks.refresh_tasks()
            return
        if not self._apply_task_update(task):
            if task.status.is_terminal:
                self._terminal_replays[task.id] = task
            if event.cursor:
                self._blocked_event_cursor = event.cursor
            return
        self._terminal_replays.pop(task.id, None)
        self._session.task_state.revisions[task.id] = event.revision
        if event.cursor:
            self._session.task_state.cursor = event.cursor
            if self._blocked_event_cursor == event.cursor:
                self._blocked_event_cursor = None

    def _apply_task_update(self, task: Task) -> bool:
        result = None
        current = self._session.binding.current
        if current is None:
            return False
        try:
            if task.status.is_terminal:
                result = current.committed_task_result(task.id)
                if result is None:
                    raise RuntimeError(
                        "服务尚未提交任务结果"
                    )
                self._session._apply_workflow_update(result.workflow)
            if result is not None and result.imported_asset_id:
                asset = current.get_asset(result.imported_asset_id)
                self._session.selection.asset_ids = [asset.id]
                if result.imported_purpose == "watermark":
                    self._session.selection.watermark_asset_id = asset.id
                    self._session._set_status(f"已选择水印 {asset.name}")
                elif result.imported_purpose == "subtitle":
                    self._session.selection.document_id = result.imported_document_id
                    self._session.selection.subtitle_segment_ids = []
                    self._session.projectors.subtitles.refresh_documents()
                    self._session.projectors.timeline.refresh_preview_subtitles()
                    segment_count = len(
                        current.list_subtitle_segments(
                            result.imported_document_id
                        )
                    )
                    self._session._set_status(f"已导入 {asset.name}，共 {segment_count} 条字幕")
                else:
                    self._session._set_status(f"已导入 {asset.name}")
                self._session.events.projectStateChanged.emit()
                self._session.events.selectionChanged.emit()
            if result is not None and result.download_plan is not None:
                self._session._set_download_plan(result.download_plan)
            if result is not None and result.sequence_bounds_status:
                if result.sequence_bounds_status == "stale":
                    self._session._set_status("分析期间时间线已修改，请重新运行智能入出点")
                else:
                    sequence = current.get_sequence(result.sequence_id)
                    note = (
                        "；未发现启用的字幕，只处理了黑屏"
                        if result.sequence_bounds_status == "applied_without_speech"
                        else ""
                    )
                    status = (
                        f"已设置序列入出点：{sequence.in_out.in_frame}–{sequence.in_out.out_frame} 帧{note}"
                    )
                    if result.sequence_id == self._session.binding.active_sequence_id:
                        self._session.binding.timeline = current.timeline(
                            self._session.binding.active_sequence_id
                        )
                        self._session._finish_sequence_in_out_edit(status)
                    else:
                        self._session._set_status(f"{status}；结果已应用到原序列")
            if result is not None and result.audio_metrics is not None:
                self._session.requests.audio_metrics_id += 1
                self._session.presentation.audio_metrics = result.audio_metrics
                self._session.events.audioMetricsChanged.emit()
        except Exception as error:
            self._report_delivery_error(
                task.id,
                task.revision,
                error,
            )
            return False
        try:
            self._finish_task_update(task, result)
        except Exception as error:
            self._report_delivery_error(
                task.id,
                task.revision,
                error,
            )
            return False
        self._reported_delivery_errors.discard((task.id, task.revision))
        return True

    def _finish_task_update(self, task: Task, result: Any) -> None:
        timeline = self._session.binding.timeline
        if task.status.is_terminal:
            imported_asset_id = result.imported_asset_id if result is not None else ""
            self._session.timeline_assets.finish_import_drop(
                task.id,
                imported_asset_id,
            )
        if not task.status.is_terminal:
            self._session.projectors.tasks.refresh_tasks()
            return
        if task.kind in {
            TaskKind.IMPORT,
            TaskKind.DOWNLOAD,
            TaskKind.PROXY,
            TaskKind.WAVEFORM,
        }:
            if task.kind == TaskKind.WAVEFORM:
                for asset_id in task.input_asset_ids:
                    self._session.asset_state.waveform_cache.pop(asset_id, None)
            self._session.projectors.assets.refresh_assets()
            if task.kind == TaskKind.WAVEFORM:
                self._session.projectors.timeline.refresh_timeline()
        if task.kind in {TaskKind.TRANSCRIBE, TaskKind.TRANSLATE}:
            if isinstance(task.command, TranscribeSequenceCommand):
                if task.command.sequence_id == self._session.binding.active_sequence_id:
                    if timeline is None:
                        raise RuntimeError("当前项目没有活动时间线")
                    timeline.reload()
                    self._session.projectors.timeline.refresh_timeline()
                    self._session.events.projectStateChanged.emit()
                self._session.projectors.assets.refresh_assets()
            self._session.projectors.subtitles.refresh_documents()
            self._session.projectors.timeline.refresh_preview_subtitles()
        if isinstance(task.command, (AnalyzeScenesCommand, TrackSubjectCommand)):
            if task.command.sequence_id == self._session.binding.active_sequence_id:
                if timeline is None:
                    raise RuntimeError("当前项目没有活动时间线")
                timeline.reload()
                self._session.projectors.timeline.refresh_timeline()
                self._session.events.projectStateChanged.emit()
                self._session.events.selectionChanged.emit()
                self._session.events.historyChanged.emit()
            if task.status.value == "completed":
                label = (
                    "场景切点已写入时间线"
                    if isinstance(task.command, AnalyzeScenesCommand)
                    else "画面跟踪已应用"
                )
                self._session._set_status(label)
        if task.kind == TaskKind.HIGHLIGHT:
            self._session.projectors.highlights.refresh_highlights()
        if task.kind in {TaskKind.PROXY, TaskKind.ANALYZE}:
            self._session.projectors.timeline.schedule_preview_graph()
        if task.kind == TaskKind.WEB_RENDER:
            self._session.projectors.timeline.schedule_preview_graph()
        self._session.projectors.workspace.refresh_recent_projects()
        self._session.projectors.tasks.refresh_tasks()
        self._session.events.workflowChanged.emit()

    def _report_delivery_error(
        self,
        task_id: str,
        revision: int,
        error: Exception,
    ) -> None:
        key = (task_id, revision)
        if key in self._reported_delivery_errors:
            return
        self._reported_delivery_errors.add(key)
        self._session.events.errorOccurred.emit(
            f"处理任务结果失败，将自动重试：{error}"
        )

    def active_workflow(self):
        return self._session.binding.current.active_workflow() if self._session.binding.current else None
