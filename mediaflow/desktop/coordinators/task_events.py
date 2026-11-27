from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal, Slot

from mediaflow.application.events import TaskEvent
from mediaflow.desktop.component_contexts import TaskOperationsContext
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.task_commands import (
    AnalyzeHighlightsCommand,
    AnalyzeLoudnessCommand,
    AnalyzeScenesCommand,
    AnalyzeSequenceBoundsCommand,
    ExportFcpxmlCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    TaskCommand,
    TrackMaskCommand,
    TrackSubjectCommand,
    TranscribeSequenceCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)
from mediaflow.domain.tasks import ProjectArchiveTaskOutcome, ProjectCollectionTaskOutcome, Task
from mediaflow.domain.workflows import WorkflowRun

from .base import SessionCoordinator

logger = logging.getLogger(__name__)


class _TaskEventBridge(QObject):
    eventReceived = Signal(object)


class TaskOperations(SessionCoordinator[TaskOperationsContext]):
    def __init__(self, context: TaskOperationsContext):
        super().__init__(context)
        self._start_lock = threading.RLock()
        self._blocked_event_cursor: int | None = None
        self._terminal_replays: dict[str, Task] = {}
        self._reported_delivery_errors: set[tuple[str, int]] = set()
        self._active_workflow: WorkflowRun | None = None
        self._active_workflow_loaded = False
        self._bridge = _TaskEventBridge(context.parent)
        self._bridge.eventReceived.connect(self._on_event)
        self._reconciliation_timer = QTimer(self)
        self._reconciliation_timer.setInterval(1_000)
        self._reconciliation_timer.timeout.connect(self._reconcile_timer_tick)

    def publish(self, envelope: object) -> None:
        self._bridge.eventReceived.emit(envelope)

    def reset_delivery_state(self) -> None:
        self._reconciliation_timer.stop()
        self._blocked_event_cursor = None
        self._terminal_replays.clear()
        self._reported_delivery_errors.clear()
        self.invalidate_active_workflow()

    def invalidate_active_workflow(self) -> None:
        self._active_workflow = None
        self._active_workflow_loaded = False

    def refresh_active_workflow(self) -> WorkflowRun | None:
        current = self._context.state.binding.current
        self._active_workflow = current.active_workflow() if current is not None else None
        self._active_workflow_loaded = True
        return self._active_workflow

    def reconcile_committed_results(self) -> None:
        current = self._context.state.binding.current
        if current is None:
            return
        candidates = dict(self._terminal_replays)
        for task in current.list_tasks():
            projected = self._context.state.tasks.items.get(task.id)
            if task.status.is_terminal and (
                task.revision > self._context.state.tasks.revisions.get(task.id, -1)
                or projected is None
                or projected.revision < task.revision
                or projected.status != task.status
            ):
                candidates[task.id] = task
        for task in candidates.values():
            if self._apply_task_update(task):
                self._terminal_replays.pop(task.id, None)
                self._context.state.tasks.revisions[task.id] = task.revision
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
            self._context.updates.report_error(str(error))
            return None

    def create(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
    ) -> Task:
        self._context.require_writable()
        current = self._context.state.binding.current
        if current is None:
            raise RuntimeError("当前没有打开的项目")
        scope = self._active_request_scope(command)
        with self._start_lock:
            task = self._active_task_for_scope(scope)
            if task is None:
                task = current.start_task(
                    command,
                    input_asset_ids,
                    sequence_id=(sequence_id or self._context.state.binding.active_sequence_id),
                )
        observed = self._context.state.tasks.items.get(task.id)
        # The synchronous service facade keeps pumping the Qt event loop while
        # start_task waits for its response. A fast task can therefore publish
        # running and terminal events before this method receives the older
        # created-task snapshot. Never let that response move the desktop back
        # to an earlier task revision.
        if observed is None or task.revision >= observed.revision:
            self._context.state.tasks.items[task.id] = task
        self._context.refresh_tasks()
        self.update_reconciliation_state()
        return task

    def update_reconciliation_state(self) -> None:
        needs_reconciliation = bool(self._terminal_replays) or any(
            task.status.is_active for task in self._context.state.tasks.items.values()
        )
        if (
            needs_reconciliation
            and self._context.state.binding.current is not None
            and not self._context.state.requests.shutting_down
            and QCoreApplication.instance() is not None
        ):
            if not self._reconciliation_timer.isActive():
                self._reconciliation_timer.start()
        else:
            self._reconciliation_timer.stop()

    @Slot()
    def _reconcile_timer_tick(self) -> None:
        if self._context.state.binding.current is None or self._context.state.requests.shutting_down:
            self._reconciliation_timer.stop()
            return
        try:
            self._context.reconcile_task_events()
        except Exception:
            logger.exception("Desktop task event reconciliation failed")
        self.update_reconciliation_state()

    def _active_task_for_scope(
        self,
        scope: tuple[str, ...] | None,
    ) -> Task | None:
        if scope is None:
            return None
        current = self._context.state.binding.current
        if current is None:
            return None
        observed: dict[str, Task] = dict(self._context.state.tasks.items)
        for task in current.list_tasks():
            observed.setdefault(task.id, task)
        return next(
            (
                task
                for task in observed.values()
                if task.status.is_active and self._active_request_scope(task.command) == scope
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
                command.mode,
            )
        if isinstance(command, TrackMaskCommand):
            return (
                "mask_tracking",
                command.sequence_id,
                command.clip_id,
                command.mask_id,
            )
        return None

    @Slot(object)
    def _on_event(self, envelope: object) -> None:
        if not isinstance(envelope, tuple) or len(envelope) != 2:
            return
        generation, event = envelope
        current = self._context.state.binding.current
        if (
            not isinstance(generation, int)
            or generation != self._context.state.binding.generation
            or not isinstance(event, TaskEvent)
            or event.project_id != self._context.state.binding.project_id
        ):
            return
        if event.cursor:
            if event.cursor <= self._context.state.tasks.cursor:
                return
            if self._blocked_event_cursor is not None and event.cursor > self._blocked_event_cursor:
                return
        previous_revision = self._context.state.tasks.revisions.get(event.task_id, -1)
        if event.event_type == "deleted":
            self._context.state.tasks.items.pop(event.task_id, None)
            self._context.state.tasks.revisions.pop(event.task_id, None)
            if event.cursor:
                self._context.state.tasks.cursor = event.cursor
                if self._blocked_event_cursor == event.cursor:
                    self._blocked_event_cursor = None
            self._context.refresh_tasks()
            self.update_reconciliation_state()
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
        if current is None:
            self._context.state.tasks.items[task.id] = task
            if task.status.is_terminal:
                self._context.finish_import_drop(
                    task.id,
                    "",
                )
                self._terminal_replays[task.id] = task
            self._context.state.tasks.revisions[task.id] = event.revision
            if event.cursor:
                self._context.state.tasks.cursor = event.cursor
                if self._blocked_event_cursor == event.cursor:
                    self._blocked_event_cursor = None
            self._context.refresh_tasks()
            self.update_reconciliation_state()
            return
        if not self._apply_task_update(task):
            if task.status.is_terminal:
                self._terminal_replays[task.id] = task
            if event.cursor:
                self._blocked_event_cursor = event.cursor
            self.update_reconciliation_state()
            return
        self._terminal_replays.pop(task.id, None)
        self._context.state.tasks.revisions[task.id] = event.revision
        if event.cursor:
            self._context.state.tasks.cursor = event.cursor
            if self._blocked_event_cursor == event.cursor:
                self._blocked_event_cursor = None
        self.update_reconciliation_state()

    def _apply_task_update(self, task: Task) -> bool:
        result = None
        current = self._context.state.binding.current
        if current is None:
            return False
        previous_task = self._context.state.tasks.items.get(task.id)
        try:
            if task.status.is_terminal:
                current.invalidate_task_result_cache()
                result = current.committed_task_result(task.id)
                if result is None:
                    raise RuntimeError("服务尚未提交任务结果")
                self._context.apply_workflow_update(result.workflow)
            if result is not None and result.imported_asset_id:
                asset = current.get_asset(result.imported_asset_id)
                self._context.state.selection.asset_ids = [asset.id]
                if result.imported_purpose == "watermark":
                    self._context.state.selection.watermark_asset_id = asset.id
                    self._context.set_status("已选择水印 %1", asset.name)
                elif result.imported_purpose == "subtitle":
                    self._context.state.selection.document_id = result.imported_document_id
                    self._context.state.selection.subtitle_segment_ids = []
                    self._context.refresh_documents()
                    self._context.refresh_preview_subtitles()
                    segment_count = len(current.list_subtitle_segments(result.imported_document_id))
                    self._context.set_status(
                        "已导入 %1，共 %2 条字幕",
                        asset.name,
                        segment_count,
                    )
                else:
                    self._context.set_status("已导入 %1", asset.name)
                self._context.updates.commit(project=True)
                self._context.updates.commit(selection=True)
            if result is not None and result.download_plan is not None:
                self._context.set_download_plan(result.download_plan)
            if result is not None and result.sequence_bounds_status:
                if result.sequence_bounds_status == "stale":
                    self._context.set_status("分析期间时间线已修改，请重新运行智能入出点")
                else:
                    sequence = current.get_sequence(result.sequence_id)
                    bounds = sequence.in_out
                    if bounds is None:
                        raise RuntimeError("序列入出点任务完成后没有返回有效范围")
                    without_speech = result.sequence_bounds_status == "applied_without_speech"
                    if result.sequence_id == self._context.state.binding.active_sequence_id:
                        self._context.state.binding.timeline = current.timeline(
                            self._context.state.binding.active_sequence_id
                        )
                        if without_speech:
                            self._context.finish_sequence_in_out_edit(
                                "已设置序列入出点：%1–%2 帧；未发现启用的字幕，只处理了黑屏",
                                bounds.in_frame,
                                bounds.out_frame,
                            )
                        else:
                            self._context.finish_sequence_in_out_edit(
                                "已设置序列入出点：%1–%2 帧",
                                bounds.in_frame,
                                bounds.out_frame,
                            )
                    else:
                        if without_speech:
                            self._context.set_status(
                                "已设置序列入出点：%1–%2 帧；未发现启用的字幕，"
                                "只处理了黑屏；结果已应用到原序列",
                                bounds.in_frame,
                                bounds.out_frame,
                            )
                        else:
                            self._context.set_status(
                                "已设置序列入出点：%1–%2 帧；结果已应用到原序列",
                                bounds.in_frame,
                                bounds.out_frame,
                            )
            if result is not None and result.audio_metrics is not None:
                self._context.state.requests.audio_metrics_id += 1
                self._context.state.presentation.audio_metrics = result.audio_metrics
                self._context.updates.commit(audio_metrics=True)
        except Exception as error:
            self._report_delivery_error(
                task.id,
                task.revision,
                error,
            )
            return False
        self._context.state.tasks.items[task.id] = task
        try:
            self._finish_task_update(task, result)
        except Exception as error:
            if previous_task is None:
                self._context.state.tasks.items.pop(task.id, None)
            else:
                self._context.state.tasks.items[task.id] = previous_task
            self._report_delivery_error(
                task.id,
                task.revision,
                error,
            )
            return False
        self._reported_delivery_errors.discard((task.id, task.revision))
        return True

    def _finish_task_update(self, task: Task, result: Any) -> None:
        timeline = self._context.state.binding.timeline
        if task.status.is_terminal:
            imported_asset_id = result.imported_asset_id if result is not None else ""
            self._context.finish_import_drop(
                task.id,
                imported_asset_id,
            )
        if not task.status.is_terminal:
            self._context.refresh_tasks(task_ids=[task.id])
            return
        if task.kind in {
            TaskKind.IMPORT,
            TaskKind.DOWNLOAD,
            TaskKind.PROXY,
            TaskKind.WAVEFORM,
        }:
            if task.kind == TaskKind.WAVEFORM:
                for asset_id in task.input_asset_ids:
                    self._context.state.assets.waveform_cache.pop(asset_id, None)
            self._context.refresh_assets()
            if task.kind == TaskKind.WAVEFORM:
                self._context.refresh_timeline()
        if task.kind in {TaskKind.TRANSCRIBE, TaskKind.TRANSLATE}:
            if isinstance(task.command, TranscribeSequenceCommand):
                if task.command.sequence_id == self._context.state.binding.active_sequence_id:
                    if timeline is None:
                        raise RuntimeError("当前项目没有活动时间线")
                    timeline.reload()
                    self._context.refresh_timeline()
                    self._context.updates.commit(project=True)
                self._context.refresh_assets()
            self._context.refresh_documents()
            self._context.refresh_preview_subtitles()
        if isinstance(
            task.command,
            (AnalyzeScenesCommand, TrackMaskCommand, TrackSubjectCommand),
        ):
            if task.command.sequence_id == self._context.state.binding.active_sequence_id:
                if timeline is None:
                    raise RuntimeError("当前项目没有活动时间线")
                timeline.reload()
                self._context.refresh_timeline()
                self._context.updates.commit(project=True)
                self._context.updates.commit(selection=True)
                self._context.updates.commit(history=True)
            if task.status.value == "completed":
                if isinstance(task.command, AnalyzeScenesCommand):
                    self._context.set_status("场景切点已写入时间线")
                elif isinstance(task.command, TrackMaskCommand):
                    self._context.set_status("蒙版跟踪已应用")
                else:
                    self._context.set_status("画面跟踪已应用")
        if task.kind == TaskKind.HIGHLIGHT:
            self._context.refresh_highlights()
        if task.kind == TaskKind.DUBBING:
            if timeline is not None:
                timeline.reload()
                self._context.refresh_timeline()
            self._context.refresh_assets()
            self._context.updates.commit(project=True)
            self._context.updates.commit(history=True)
        if task.kind in {TaskKind.PROXY, TaskKind.ANALYZE}:
            self._context.schedule_preview()
        if task.kind == TaskKind.WEB_RENDER:
            self._context.schedule_preview()
        if task.status == TaskStatus.COMPLETED:
            if isinstance(task.outcome, ProjectCollectionTaskOutcome):
                collected = task.outcome.result
                self._context.updates.commit(project=True, history=True)
                self._context.set_status(
                    "已归集 %1 个外部素材，共 %2 字节", len(collected.assets), collected.record.total_bytes
                )
            elif isinstance(task.outcome, ProjectArchiveTaskOutcome):
                archive = task.outcome.result
                self._context.set_status(
                    "可迁移项目已归档：%1（%2 个文件）", archive.destination, archive.file_count
                )
            elif isinstance(task.command, ExportFcpxmlCommand):
                self._context.set_status("已导出 FCPXML：%1", task.command.output_path)
        self._context.refresh_recent_projects()
        self._context.refresh_tasks()
        self._context.updates.commit(workflow=True)

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
        self._context.updates.report_error(f"处理任务结果失败，将自动重试：{error}")

    def active_workflow(self) -> WorkflowRun | None:
        return self._active_workflow if self._active_workflow_loaded else None
