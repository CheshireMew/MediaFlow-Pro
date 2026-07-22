from __future__ import annotations

import copy
import logging
import os
import sys
import threading
import uuid
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot

from mediaflow.application.events import TaskEvent
from mediaflow.application.project_workflow_service import ProjectWorkflowService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.workflow_stage_handlers import WorkflowUpdate
from mediaflow.composition import EditorApplication, EditorProject
from mediaflow.desktop.models import (
    AssetFilterModel,
    AssetListModel,
    AudioBusListModel,
    AudioEffectListModel,
    AudioEffectParameterListModel,
    ClipListModel,
    DownloadEntryListModel,
    GlossaryTermListModel,
    HighlightListModel,
    LlmProviderListModel,
    RecentProjectListModel,
    SequenceListModel,
    SubtitleDocumentListModel,
    SubtitlePlacementListModel,
    SubtitleSegmentListModel,
    TaskListModel,
    TimelineMarkerListModel,
    TimelineRangeListModel,
    TrackListModel,
    TransitionListModel,
    WebLayerListModel,
)
from mediaflow.domain.downloads import DownloadPlan, DownloadRequest
from mediaflow.domain.enums import (
    AssetKind,
    ColorMode,
    TaskKind,
    TrackKind,
    WorkflowStage,
)
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import (
    GlobalSettings,
)
from mediaflow.domain.task_commands import (
    GenerateProxyCommand,
    GenerateWaveformCommand,
    TaskCommand,
    TranscribeSequenceCommand,
)
from mediaflow.domain.tasks import Task
from mediaflow.domain.timebase import (
    frames_to_seconds,
    seconds_to_frames,
)

from .controller_facet import CONTROLLER_SIGNALS
from .project_presenter import ProjectPresentationProjector

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mediaflow.infrastructure.project_repository import ProjectRepository


@dataclass(frozen=True)
class _TimelinePlacement:
    track_id: str = ""
    start_frame: int | None = None
    pixels_per_frame: float = 3.0
    playhead_frame: int = 0
    snap_enabled: bool = True
    force_new_track: bool = False


@dataclass(frozen=True)
class _PlacedTimelineAsset:
    track_id: str
    end_frame: int


@dataclass
class _ImportDropBatch:
    placement: _TimelinePlacement
    asset_ids: list[str | None]
    pending_task_ids: set[str] = dataclass_field(default_factory=set)


class _TaskSignalBridge(QObject):
    eventReceived = Signal(object)


class _RuntimeToolSignalBridge(QObject):
    eventReceived = Signal(object)


class _BackgroundSignalBridge(QObject):
    resultReceived = Signal(object)


class ProjectSession(QObject):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    audioMetricsChanged = Signal()
    workflowChanged = Signal()
    downloadPlanChanged = Signal()
    runtimeToolsChanged = Signal()
    waveformDataChanged = Signal(str)
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        application: EditorApplication | None = None,
    ):
        super().__init__(parent)
        self._api = application or EditorApplication()
        self._projector = ProjectPresentationProjector(self)
        self._controllers: dict[str, QObject] = {}
        self._broadcasting = False
        self.settings = self._api.settings
        self._project: EditorProject | None = None
        self._project_id = ""
        self._session_generation = 0
        self._task_subscription_token: int | None = None
        self._task_revisions: dict[str, int] = {}
        self._task_view: dict[str, Task] = {}
        self._documents: ProjectRepository | None = None
        self._assets = None
        self._subtitle_editor: SubtitleEditingService | None = None
        self._subtitle_publication: SubtitlePublicationService | None = None
        self._editor: TimelineEditor | None = None
        self._tasks: TaskService | None = None
        self._workflows: ProjectWorkflowService | None = None
        self._active_sequence_id = ""
        self._selected_asset_ids: list[str] = []
        self._selected_clip_ids: list[str] = []
        self._selected_document_id = ""
        self._selected_subtitle_segment_ids: list[str] = []
        self._selected_subtitle_placement_id = ""
        self._selected_glossary_term_id = ""
        self._selected_llm_provider_id = ""
        self._selected_highlight_id = ""
        self._selected_audio_bus_id = ""
        self._selected_audio_effect_id = ""
        self._selected_transition_id = ""
        self._selected_marker_id = ""
        self._selected_range_id = ""
        self._selected_watermark_asset_id = ""
        self._range_in_frame: int | None = None
        self._status_message = ""
        self._last_error_id = ""
        self._preview_graph_path = ""
        self._hdr_preview_active = False
        self._pending_profile_asset_id = ""
        self._pending_profile_label = ""
        self._pending_profile_placement = _TimelinePlacement()
        self._pending_asset_batch_ids: list[str] = []
        self._pending_asset_batch_placement = _TimelinePlacement()
        self._pending_import_drop_tasks: dict[str, tuple[str, int]] = {}
        self._pending_import_drop_batches: dict[str, _ImportDropBatch] = {}
        self._pending_relink_asset_id = ""
        self._pending_relink_path = ""
        self._preview_subtitles: list[tuple[int, int, str]] = []
        self._preview_subtitles_by_track: dict[str, list[tuple[int, int, str]]] = {}
        self._waveform_cache: dict[str, tuple[str, int, dict]] = {}
        self._waveform_pending: set[tuple[int, str, str]] = set()
        self._asset_thumbnail_paths: dict[str, str] = {}
        self._asset_thumbnail_request_id = 0
        self._asset_thumbnail_pending_request: tuple[int, int, str] | None = None
        self._asset_thumbnail_refresh_requested = False
        self._audio_metrics: dict = {}
        self._audio_metrics_request_id = 0
        self._video_encoder_options: list[dict] = []
        self._home_summary: dict = {
            "runningTaskCount": 0,
            "failedTaskCount": 0,
            "offlineAssetCount": 0,
            "pendingWorkflowCount": 0,
            "recentArtifactCount": 0,
        }
        self._download_plan: DownloadPlan | None = None
        self._download_entry_selection: set[int] = set()
        self._download_analysis_request_id = 0
        self._download_analysis_busy = False
        self._cookie_status: dict = {}
        self._runtime_tool_status = {
            **self._api.runtime_tool_status(),
            "busy": False,
            "progress": 0.0,
            "message": "",
            "operation": "",
        }
        self._runtime_tool_cancel = threading.Event()
        self._runtime_tool_thread: threading.Thread | None = None
        self._background_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mediaflow-desktop-io",
        )
        self._preview_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mediaflow-preview-compile",
        )
        self._background_bridge = _BackgroundSignalBridge(self)
        self._background_bridge.resultReceived.connect(self._on_background_result)
        self._recent_request_id = 0
        self._preview_request_id = 0
        self._encoder_request_id = 0
        self._shutting_down = False
        self._pending_preview_range: tuple[int, int] | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._projector.compile_preview_graph)
        self._project_revision_timer = QTimer(self)
        self._project_revision_timer.setInterval(500)
        self._project_revision_timer.timeout.connect(self._poll_external_project_changes)
        self._task_bridge = _TaskSignalBridge(self)
        self._task_bridge.eventReceived.connect(self._on_task_event)
        self.errorOccurred.connect(self._log_ui_error)
        self._runtime_tool_bridge = _RuntimeToolSignalBridge(self)
        self._runtime_tool_bridge.eventReceived.connect(self._on_runtime_tool_event)

        self._asset_model = AssetListModel(self)
        self._filtered_asset_model = AssetFilterModel(self._asset_model, self)
        self._sequence_model = SequenceListModel(self)
        self._recent_project_model = RecentProjectListModel(self)
        self._download_entry_model = DownloadEntryListModel(self)
        self._track_model = TrackListModel(self)
        self._clip_model = ClipListModel(self)
        self._transition_model = TransitionListModel(self)
        self._marker_model = TimelineMarkerListModel(self)
        self._range_model = TimelineRangeListModel(self)
        self._web_layer_model = WebLayerListModel(self)
        self._task_model = TaskListModel(self)
        self._document_model = SubtitleDocumentListModel(self)
        self._segment_model = SubtitleSegmentListModel(self)
        self._subtitle_placement_model = SubtitlePlacementListModel(self)
        self._glossary_model = GlossaryTermListModel(self)
        self._llm_provider_model = LlmProviderListModel(self)
        self._highlight_model = HighlightListModel(self)
        self._audio_bus_model = AudioBusListModel(self)
        self._audio_effect_model = AudioEffectListModel(self)
        self._audio_effect_parameter_model = AudioEffectParameterListModel(self)
        self._projector.refresh_settings_models()
        self._projector.refresh_recent_projects()
        self._projector.discover_video_encoders()

    def _attach_controllers(self, controllers: dict[str, QObject]) -> None:
        if self._controllers:
            raise RuntimeError("Project controllers are already attached")
        self._controllers = dict(controllers)
        for signal_name in CONTROLLER_SIGNALS:
            getattr(self, signal_name).connect(
                lambda *args, name=signal_name: self._broadcast_signal(None, name, *args)
            )
            for controller in self._controllers.values():
                getattr(controller, signal_name).connect(
                    lambda *args, source=controller, name=signal_name: self._broadcast_signal(
                        source,
                        name,
                        *args,
                    )
                )

    def _broadcast_signal(
        self,
        source: QObject | None,
        signal_name: str,
        *args,
    ) -> None:
        if self._broadcasting:
            return
        self._broadcasting = True
        try:
            for controller in self._controllers.values():
                if controller is not source:
                    getattr(controller, signal_name).emit(*args)
        finally:
            self._broadcasting = False

    def __getattr__(self, name: str):
        controllers = self.__dict__.get("_controllers", {})
        for controller in controllers.values():
            if hasattr(type(controller), name):
                return getattr(controller, name)
        raise AttributeError(name)

    def _start_runtime_tool_operation(self, operation: str) -> None:
        if self._runtime_tool_thread and self._runtime_tool_thread.is_alive():
            self.errorOccurred.emit("已有运行时工具操作正在执行")
            return
        self._runtime_tool_cancel.clear()
        self._runtime_tool_status = {
            **self._runtime_tool_status,
            "busy": True,
            "progress": 0.0,
            "message": "starting",
            "operation": operation,
        }
        self.runtimeToolsChanged.emit()

        def check_cancelled() -> None:
            if self._runtime_tool_cancel.is_set():
                raise RuntimeError("运行时工具操作已取消")

        def report(progress: float, message: str) -> None:
            self._runtime_tool_bridge.eventReceived.emit(
                {
                    "type": "progress",
                    "progress": float(progress),
                    "message": message,
                    "operation": operation,
                }
            )

        def run() -> None:
            try:
                result = self._api.run_runtime_tool(
                    operation,
                    progress=report,
                    check_cancelled=check_cancelled,
                )
                self._runtime_tool_bridge.eventReceived.emit(
                    {"type": "completed", "operation": operation, "result": result}
                )
            except Exception as error:
                self._runtime_tool_bridge.eventReceived.emit(
                    {
                        "type": "cancelled" if self._runtime_tool_cancel.is_set() else "failed",
                        "operation": operation,
                        "error": str(error),
                    }
                )

        self._runtime_tool_thread = threading.Thread(
            target=run,
            name=f"mediaflow-runtime-{operation}",
            daemon=True,
        )
        self._runtime_tool_thread.start()

    @Slot(object)
    def _on_runtime_tool_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "progress":
            self._runtime_tool_status = {
                **self._runtime_tool_status,
                "progress": max(0.0, min(100.0, float(event.get("progress", 0)))),
                "message": str(event.get("message") or ""),
            }
            self.runtimeToolsChanged.emit()
            return
        operation = str(event.get("operation") or "")
        if event_type == "completed" and operation == "install_asr_cli":
            candidate = self.settings.model_copy(deep=True)
            candidate.asr.cli_path = str(event.get("result") or "") or None
            self._commit_settings(candidate)
        if event_type == "completed" and operation == "inspect":
            self._runtime_tool_status = {
                **self._runtime_tool_status,
                **dict(event.get("result") or {}),
            }
        self._projector.refresh_runtime_tool_status(preserve_cuda=True)
        if event_type == "failed":
            self.errorOccurred.emit(str(event.get("error") or "运行时工具操作失败"))
        elif event_type == "cancelled":
            self._set_status("运行时工具操作已取消")
        else:
            self._set_status("运行时工具操作已完成")

    def _continue_asset_batch(self) -> None:
        while self._pending_asset_batch_ids and not self._pending_profile_asset_id:
            asset_id = self._pending_asset_batch_ids.pop(0)
            placed = self._add_asset_to_timeline(
                asset_id,
                self._pending_asset_batch_placement,
            )
            if placed is None:
                return
            if self._pending_asset_batch_placement.start_frame is not None:
                self._pending_asset_batch_placement = replace(
                    self._pending_asset_batch_placement,
                    track_id=placed.track_id,
                    start_frame=placed.end_frame,
                    force_new_track=False,
                )

    def _queue_assets_for_timeline(
        self,
        asset_ids: Iterable[str],
        placement: _TimelinePlacement | None = None,
    ) -> None:
        self._pending_asset_batch_ids = list(dict.fromkeys(asset_ids))
        self._pending_asset_batch_placement = placement or _TimelinePlacement()
        self._continue_asset_batch()

    def _add_asset_to_timeline(
        self,
        asset_id: str,
        placement: _TimelinePlacement,
    ) -> _PlacedTimelineAsset | None:
        asset = self._documents.get_asset(asset_id)
        project = self._documents.get_project()
        if asset.kind == AssetKind.VIDEO and self._active_sequence_id == project.main_sequence_id:
            state = self._editor.state
            assets = {item.id: item for item in self._documents.list_assets()}
            has_timeline_video = any(assets[item.asset_id].kind == AssetKind.VIDEO for item in state.clips)
            if not has_timeline_video:
                suggested = self._assets.suggested_profile(asset.id)
                if suggested and suggested != state.sequence.profile:
                    if state.clips and state.sequence.profile_confirmed:
                        fps = suggested.fps_numerator / suggested.fps_denominator
                        mode = "HDR10" if suggested.color_mode == ColorMode.HDR10_BT2020_PQ else "SDR"
                        self._pending_profile_asset_id = asset.id
                        self._pending_profile_placement = placement
                        self._pending_profile_label = (
                            f"{suggested.width}×{suggested.height}  {fps:.3f} fps  {mode}"
                        ).replace(".000", "")
                        self.profileConfirmationChanged.emit()
                        return None
                    self._assets.adopt_main_profile_from_video(asset.id)
                    self._editor.reload()
                    asset = self._documents.get_asset(asset.id)
                    self.projectStateChanged.emit()
                elif not state.sequence.profile_confirmed:
                    self._assets.adopt_main_profile_from_video(asset.id)
                    self._editor.reload()
                    asset = self._documents.get_asset(asset.id)
                    self.projectStateChanged.emit()
        return self._place_asset_on_timeline(asset, placement)

    def _place_asset_on_timeline(
        self,
        asset,
        placement: _TimelinePlacement,
    ) -> _PlacedTimelineAsset:
        if asset.kind == AssetKind.SUBTITLE:
            documents = self._documents.list_subtitle_documents(asset.id)
            if not documents:
                raise RuntimeError("字幕素材还没有对应的字幕文档，请重新导入 SRT")
            document = next(
                (item for item in documents if item.id == self._selected_document_id),
                documents[0],
            )
            segments = self._documents.list_subtitle_segments(document.id)
            if not segments:
                raise RuntimeError("字幕文档中没有可放置的字幕")
            source_start = min(item.start_frame for item in segments)
            source_end = max(item.end_frame for item in segments)
            start = self._placement_start(placement, source_start)
            duration = max(1, source_end - source_start)
            subtitle_track = self._resolve_drop_track(
                TrackKind.SUBTITLE,
                placement,
                start,
                duration,
            )
            placements = self._documents.place_subtitle_document(
                document.id,
                subtitle_track.id,
                offset_frames=start - source_start,
                follow_clips=False,
            )
            self._selected_document_id = document.id
            self._selected_clip_ids = []
            self._projector.refresh_all()
            self.selectionChanged.emit()
            self._projector.schedule_preview_graph()
            self._set_status(f"已放入 {len(placements)} 条字幕")
            return _PlacedTimelineAsset(
                track_id=subtitle_track.id,
                end_frame=start + duration,
            )
        target_kind = {
            AssetKind.VIDEO: TrackKind.VIDEO,
            AssetKind.IMAGE: TrackKind.VIDEO,
            AssetKind.WEB: TrackKind.VIDEO,
            AssetKind.AUDIO: TrackKind.AUDIO,
        }[asset.kind]
        duration = asset.metadata.duration_frames or 150
        project = self._documents.get_project()
        if self._active_sequence_id != project.main_sequence_id and asset.metadata.duration_frames:
            main_profile = self._documents.get_sequence(project.main_sequence_id).profile
            active_profile = self._editor.state.sequence.profile
            duration = seconds_to_frames(
                frames_to_seconds(
                    asset.metadata.duration_frames,
                    main_profile.fps_numerator,
                    main_profile.fps_denominator,
                ),
                active_profile.fps_numerator,
                active_profile.fps_denominator,
            )
        start = self._placement_start(placement, 0)
        track = self._resolve_drop_track(target_kind, placement, start, duration)
        if placement.start_frame is None:
            clips = self._editor.state.clips_for_track(track.id)
            start = max((clip.timeline_end for clip in clips), default=0)
        clip = self._editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=start,
            source_in=0,
            duration=duration,
        )
        self._selected_clip_ids = [clip.id]
        self._projector.refresh_all()
        self.selectionChanged.emit()
        self._schedule_asset_background(asset, dropped_frames=0)
        self._projector.schedule_preview_graph()
        self.historyChanged.emit()
        self._set_status(f"已将 {asset.name} 放入时间轴")
        return _PlacedTimelineAsset(track_id=track.id, end_frame=clip.timeline_end)

    def _placement_start(self, placement: _TimelinePlacement, fallback: int) -> int:
        if placement.start_frame is None:
            return fallback
        requested = max(0, placement.start_frame)
        if not placement.snap_enabled:
            return requested
        return self._editor.snap_frame(
            requested,
            self._timeline_snap_targets([], placement.playhead_frame),
            self._snap_tolerance_frames(placement.pixels_per_frame),
        )

    def _resolve_drop_track(
        self,
        kind: TrackKind,
        placement: _TimelinePlacement,
        start: int,
        duration: int,
    ):
        state = self._editor.state
        requested = next(
            (track for track in state.tracks if track.id == placement.track_id),
            None,
        )
        if (
            requested is not None
            and requested.kind == kind
            and not requested.locked
            and self._track_interval_available(requested.id, kind, start, duration)
        ):
            return requested
        if not placement.force_new_track and not placement.track_id:
            compatible = [
                track
                for track in state.tracks
                if track.kind == kind and not track.locked
            ]
            if placement.start_frame is None:
                if compatible:
                    return compatible[0]
            else:
                available = next(
                    (
                        track
                        for track in compatible
                        if self._track_interval_available(track.id, kind, start, duration)
                    ),
                    None,
                )
                if available is not None:
                    return available
        return self._add_timeline_track(kind)

    def _track_interval_available(
        self,
        track_id: str,
        kind: TrackKind,
        start: int,
        duration: int,
    ) -> bool:
        end = start + duration
        if kind == TrackKind.SUBTITLE:
            occupied = self._documents.list_subtitle_placements(track_id)
            return all(end <= item.start_frame or start >= item.end_frame for item in occupied)
        return all(
            end <= clip.timeline_start or start >= clip.timeline_end
            for clip in self._editor.state.clips_for_track(track_id)
        )

    def _add_timeline_track(self, kind: TrackKind):
        audio_bus_id = None
        if kind in {TrackKind.VIDEO, TrackKind.AUDIO}:
            buses = self._documents.list_audio_buses(self._active_sequence_id)
            preferred_name = "音乐" if kind == TrackKind.AUDIO else "对白"
            audio_bus_id = next(
                (bus.id for bus in buses if bus.name == preferred_name),
                next((bus.id for bus in buses if bus.parent_bus_id is None), None),
            )
        return self._editor.add_track(kind, audio_bus_id=audio_bus_id)

    def _start_media_import(self, source: Path) -> Task:
        if source.suffix.lower() in {".srt", ".vtt", ".ass", ".ssa"}:
            selected_media_id = next(
                (
                    asset_id
                    for asset_id in self._selected_asset_ids
                    if self._documents.get_asset(asset_id).kind
                    in {AssetKind.VIDEO, AssetKind.AUDIO}
                ),
                None,
            )
            return self._project.import_asset(
                source,
                sequence_id=self._active_sequence_id,
                purpose="subtitle",
                language=self.settings.asr.language,
                media_asset_id=selected_media_id,
            )
        return self._project.import_asset(source, sequence_id=self._active_sequence_id)

    def _import_media_paths(
        self,
        path_values: Iterable[object],
        *,
        placement: _TimelinePlacement | None = None,
    ) -> None:
        self._require_writable()
        sources = [self._local_path_value(value) for value in path_values]
        if not sources:
            return
        invalid = [source for source in sources if not source.is_file()]
        if invalid:
            raise ValueError(f"只能导入文件：{invalid[0]}")
        tasks = [self._start_media_import(source) for source in sources]
        if placement is not None:
            batch_id = uuid.uuid4().hex
            batch = _ImportDropBatch(
                placement=placement,
                asset_ids=[None] * len(tasks),
                pending_task_ids={task.id for task in tasks},
            )
            self._pending_import_drop_batches[batch_id] = batch
            for index, task in enumerate(tasks):
                self._pending_import_drop_tasks[task.id] = (batch_id, index)
        self._projector.refresh_tasks()
        label = sources[0].name if len(sources) == 1 else f"{len(sources)} 个文件"
        self._set_status(f"正在导入 {label}")

    def _finish_import_drop(self, task_id: str, asset_id: str) -> None:
        task_entry = self._pending_import_drop_tasks.pop(task_id, None)
        if task_entry is None:
            return
        batch_id, index = task_entry
        batch = self._pending_import_drop_batches.get(batch_id)
        if batch is None:
            return
        batch.pending_task_ids.discard(task_id)
        batch.asset_ids[index] = asset_id or None
        if batch.pending_task_ids:
            return
        self._pending_import_drop_batches.pop(batch_id, None)
        imported_ids = [item for item in batch.asset_ids if item]
        if imported_ids:
            self._queue_assets_for_timeline(imported_ids, batch.placement)

    @staticmethod
    def _local_path_value(value: object) -> Path:
        if isinstance(value, QUrl):
            candidate = value.toLocalFile() if value.isLocalFile() else value.toString()
        else:
            candidate = str(value)
        url = QUrl(candidate)
        path = url.toLocalFile() if url.isLocalFile() else candidate
        return Path(path).expanduser().resolve()

    def _schedule_asset_background(self, asset, *, dropped_frames: int) -> None:
        if not self._tasks:
            return
        prepare_media_managed = dropped_frames <= 0 and self._documents and any(
            run.stage == WorkflowStage.PREPARE_MEDIA and asset.id in run.asset_ids
            for run in self._documents.list_workflow_runs(active_only=True)
        )
        active = {
            (task.kind, tuple(task.input_asset_ids))
            for task in self._tasks.list()
            if task.status.value in {"pending", "running", "paused"}
        }
        proxy_key = (TaskKind.PROXY, (asset.id,))
        decision = self._project.proxy_decision(asset, dropped_frames=dropped_frames)
        if (
            self.settings.preview.automatic_proxy
            and not prepare_media_managed
            and not asset.proxy_path
            and decision.required
            and proxy_key not in active
        ):
            self._start_task(
                GenerateProxyCommand(
                    asset_id=asset.id,
                    reasons=list(decision.reasons),
                ),
                [asset.id],
            )
        waveform_key = (TaskKind.WAVEFORM, (asset.id,))
        if asset.metadata.has_audio and not asset.waveform_path and waveform_key not in active:
            self._start_task(
                GenerateWaveformCommand(asset_id=asset.id),
                [asset.id],
            )

    def _replace_project(self, candidate: EditorProject) -> None:
        try:
            project_document = candidate.documents.get_project()
            candidate.timeline(project_document.main_sequence_id)
            candidate.tasks.list()
            candidate.workflows.reconcile_interrupted()
        except Exception:
            candidate.close()
            raise
        previous = self._project
        previous_subscription = self._task_subscription_token
        preserved_selection = self._capture_project_selection()
        try:
            self._bind(candidate)
        except Exception:
            if self._tasks and self._task_subscription_token is not None:
                self._tasks.events.unsubscribe(self._task_subscription_token)
            candidate.close()
            if previous is None:
                self._project = None
                self._close_current(close_in_background=False)
            else:
                if previous_subscription is not None:
                    previous.tasks.events.unsubscribe(previous_subscription)
                self._restore_project_selection(preserved_selection)
                self._bind(previous, reset_selection=False)
            raise
        if previous is not None:
            if previous_subscription is not None:
                previous.tasks.events.unsubscribe(previous_subscription)
            self._dispose_project(previous, close_in_background=True)
        self._remember_recent_project(candidate.project_dir)

    def _create_and_open_project(
        self,
        parent: Path,
        name: str,
        *,
        profile: ProjectProfile | None = None,
        ensure_unique: bool = False,
    ) -> None:
        root, display_name = self._project_creation_target(
            parent,
            name,
            ensure_unique=ensure_unique,
        )
        candidate = self._api.create_project(root, display_name, profile)
        self._replace_project(candidate)

    def _project_creation_target(
        self,
        parent: Path,
        name: str,
        *,
        ensure_unique: bool = False,
    ) -> tuple[Path, str]:
        display_name = name.strip()
        if not display_name:
            sequence = 1
            while True:
                display_name = f"未命名项目 {sequence}"
                root = parent / self._safe_project_name(display_name)
                if not root.exists():
                    return root, display_name
                sequence += 1
        root = parent / self._safe_project_name(display_name)
        if not ensure_unique:
            return root, display_name
        suffix = 2
        while root.exists():
            candidate_name = f"{display_name} ({suffix})"
            root = parent / self._safe_project_name(candidate_name)
            suffix += 1
        if suffix > 2:
            display_name = f"{display_name} ({suffix - 1})"
        return root, display_name

    def _bind(self, project: EditorProject, *, reset_selection: bool = True) -> None:
        self._session_generation += 1
        generation = self._session_generation
        if reset_selection:
            self._reset_project_selection()
        self._project = project
        self._documents = project.documents
        self._assets = project.assets
        self._subtitle_editor = project.subtitle_editing
        self._subtitle_publication = project.subtitle_publication
        current = self._documents.get_project()
        self._project_id = current.id
        self._active_sequence_id = current.main_sequence_id
        self._editor = project.timeline(self._active_sequence_id)
        self._tasks = project.tasks
        self._workflows = project.workflows
        self._project_revision_timer.start()
        initial_tasks = self._tasks.list()
        self._task_view = {task.id: task for task in initial_tasks}
        self._task_revisions = {task.id: task.revision for task in initial_tasks}
        self._task_subscription_token = self._tasks.events.subscribe(
            lambda event: self._task_bridge.eventReceived.emit((generation, event)),
            include_snapshot=False,
        )
        self._projector.refresh_all()

    @Slot()
    def _poll_external_project_changes(self) -> None:
        if not self._project or not self._documents or self._shutting_down:
            return
        try:
            revision = self._documents.content_revision()
            if revision == self._documents.known_content_revision:
                return
            self._project.reload_external_changes()
            available_sequences = {item.id for item in self._documents.list_sequences()}
            if self._active_sequence_id not in available_sequences:
                self._active_sequence_id = self._documents.get_project().main_sequence_id
            self._editor = self._project.timeline(self._active_sequence_id)
            self._projector.refresh_all()
            self.selectionChanged.emit()
            self._projector.schedule_preview_graph()
            self._set_status("已同步 CLI 或其它进程提交的项目修改")
        except Exception as error:
            self.errorOccurred.emit(f"无法同步外部项目修改：{error}")

    @staticmethod
    def _project_selection_fields() -> tuple[str, ...]:
        return (
            "_selected_asset_ids",
            "_selected_clip_ids",
            "_selected_document_id",
            "_selected_subtitle_segment_ids",
            "_selected_subtitle_placement_id",
            "_selected_highlight_id",
            "_selected_audio_bus_id",
            "_selected_audio_effect_id",
            "_selected_transition_id",
            "_selected_marker_id",
            "_selected_range_id",
            "_selected_watermark_asset_id",
            "_range_in_frame",
            "_pending_profile_asset_id",
            "_pending_profile_label",
            "_pending_profile_placement",
            "_pending_asset_batch_ids",
            "_pending_asset_batch_placement",
            "_pending_import_drop_tasks",
            "_pending_import_drop_batches",
            "_pending_relink_asset_id",
            "_pending_relink_path",
            "_download_plan",
            "_download_entry_selection",
            "_pending_preview_range",
        )

    def _capture_project_selection(self) -> dict[str, object]:
        return {field: copy.deepcopy(getattr(self, field)) for field in self._project_selection_fields()}

    def _restore_project_selection(self, values: dict[str, object]) -> None:
        for field in self._project_selection_fields():
            setattr(self, field, copy.deepcopy(values[field]))

    def _reset_project_selection(self) -> None:
        self._selected_asset_ids = []
        self._selected_clip_ids = []
        self._selected_document_id = ""
        self._selected_subtitle_segment_ids = []
        self._selected_subtitle_placement_id = ""
        self._selected_highlight_id = ""
        self._selected_audio_bus_id = ""
        self._selected_audio_effect_id = ""
        self._selected_transition_id = ""
        self._selected_marker_id = ""
        self._selected_range_id = ""
        self._selected_watermark_asset_id = ""
        self._range_in_frame = None
        self._pending_profile_asset_id = ""
        self._pending_profile_label = ""
        self._pending_profile_placement = _TimelinePlacement()
        self._pending_asset_batch_ids = []
        self._pending_asset_batch_placement = _TimelinePlacement()
        self._pending_import_drop_tasks = {}
        self._pending_import_drop_batches = {}
        self._pending_relink_asset_id = ""
        self._pending_relink_path = ""
        self._download_plan = None
        self._download_entry_selection = set()
        self._pending_preview_range = None

    def _remember_recent_project(self, project_dir: Path) -> None:
        project_path = str(project_dir.expanduser().resolve())
        project_key = self._recent_project_key(project_path)
        candidate = self.settings.model_copy(deep=True)
        candidate.ui.recent_project_paths = [
            project_path,
            *(
                path
                for path in candidate.ui.recent_project_paths
                if self._recent_project_key(path) != project_key
            ),
        ][:10]
        try:
            self._commit_settings(candidate)
        except Exception as error:
            self.errorOccurred.emit(f"项目已打开，但无法更新最近项目记录：{error}")
        self._projector.refresh_recent_projects()

    def _forget_recent_project(self, project_dir: Path) -> bool:
        project_key = self._recent_project_key(project_dir)
        remaining = [
            path
            for path in self.settings.ui.recent_project_paths
            if self._recent_project_key(path) != project_key
        ]
        if len(remaining) == len(self.settings.ui.recent_project_paths):
            return False
        candidate = self.settings.model_copy(deep=True)
        candidate.ui.recent_project_paths = remaining
        self._commit_settings(candidate)
        self._projector.refresh_recent_projects()
        return True

    @staticmethod
    def _recent_project_key(path: str | Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def _start_task(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
    ) -> Task | None:
        try:
            return self._create_task(
                command,
                input_asset_ids,
                sequence_id=sequence_id,
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))
            return None

    def _create_task(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
    ) -> Task:
        self._require_writable()
        task = self._project.start_task(
            command,
            input_asset_ids,
            sequence_id=sequence_id or self._active_sequence_id,
        )
        self._task_view[task.id] = task
        self._projector.refresh_tasks()
        return task

    @Slot(object)
    def _on_task_event(self, envelope: object) -> None:
        try:
            generation, event = envelope
        except (TypeError, ValueError):
            return
        if (
            generation != self._session_generation
            or not isinstance(event, TaskEvent)
            or event.project_id != self._project_id
        ):
            return
        previous_revision = self._task_revisions.get(event.task_id, -1)
        if event.event_type == "deleted":
            self._task_revisions.pop(event.task_id, None)
        elif event.revision <= previous_revision:
            return
        else:
            self._task_revisions[event.task_id] = event.revision
        task = None
        if event.event_type != "deleted":
            try:
                task = Task.model_validate(event.payload)
            except (TypeError, ValueError):
                return
        result = None
        try:
            terminal = task is not None and task.status.is_consumable
            if terminal:
                result = self._project.consume_task_result(task)
                self._apply_workflow_update(result.workflow)
            else:
                result = None
            if result is not None and result.imported_asset_id:
                asset = self._documents.get_asset(result.imported_asset_id)
                self._selected_asset_ids = [asset.id]
                if result.imported_purpose == "watermark":
                    self._selected_watermark_asset_id = asset.id
                    self._projector.refresh_assets()
                    self.projectStateChanged.emit()
                    self._set_status(f"已选择水印 {asset.name}")
                elif result.imported_purpose == "subtitle":
                    self._selected_document_id = result.imported_document_id
                    self._selected_subtitle_segment_ids = []
                    self._projector.refresh_all()
                    segment_count = len(self._documents.list_subtitle_segments(result.imported_document_id))
                    self._set_status(f"已导入 {asset.name}，共 {segment_count} 条字幕")
                else:
                    self._projector.refresh_all()
                    self._set_status(f"已导入 {asset.name}")
                self.selectionChanged.emit()
            if result is not None and result.download_plan is not None:
                self._set_download_plan(result.download_plan)
                self.downloadPlanChanged.emit()
            if result is not None and result.sequence_bounds_status:
                if result.sequence_bounds_status == "stale":
                    self._set_status("分析期间时间线已修改，请重新运行智能入出点")
                else:
                    sequence = self._documents.get_sequence(result.sequence_id)
                    note = (
                        "；未发现启用的字幕，只处理了黑屏"
                        if result.sequence_bounds_status == "applied_without_speech"
                        else ""
                    )
                    status = (
                        f"已设置序列入出点：{sequence.in_out.in_frame}–{sequence.in_out.out_frame} 帧{note}"
                    )
                    if result.sequence_id == self._active_sequence_id:
                        self._editor = self._project.timeline(self._active_sequence_id)
                        self._finish_sequence_in_out_edit(status)
                    else:
                        self._set_status(f"{status}；结果已应用到原序列")
            if result is not None and result.audio_metrics is not None:
                self._audio_metrics_request_id += 1
                self._audio_metrics = result.audio_metrics
                self.audioMetricsChanged.emit()
        except (KeyError, OSError, RuntimeError, StopIteration, ValueError) as error:
            self.errorOccurred.emit(f"处理任务结果失败：{error}")
        if task is not None and task.status.is_consumable:
            imported_asset_id = result.imported_asset_id if result is not None else ""
            self._finish_import_drop(task.id, imported_asset_id)
        if event.event_type == "deleted":
            self._task_view.pop(event.task_id, None)
        elif task is not None:
            self._task_view[task.id] = task
        if task is None or not task.status.is_consumable:
            self._projector.refresh_tasks()
            return
        if task.kind in {
            TaskKind.IMPORT,
            TaskKind.DOWNLOAD,
            TaskKind.PROXY,
            TaskKind.WAVEFORM,
        }:
            if task.kind == TaskKind.WAVEFORM:
                for asset_id in task.input_asset_ids:
                    self._waveform_cache.pop(asset_id, None)
            self._projector.refresh_assets()
            if task.kind == TaskKind.WAVEFORM:
                self._projector.refresh_timeline()
        if task.kind in {TaskKind.TRANSCRIBE, TaskKind.TRANSLATE}:
            if isinstance(task.command, TranscribeSequenceCommand):
                documents = [
                    document
                    for document in self._documents.list_subtitle_documents(
                        sequence_id=task.command.sequence_id
                    )
                    if document.is_source and document.source_document_id is None
                ]
                if documents:
                    self._selected_document_id = documents[-1].id
                    self._selected_subtitle_segment_ids = []
                self._projector.refresh_assets()
            self._projector.refresh_documents()
            self._projector.refresh_preview_subtitles()
        if task.kind == TaskKind.HIGHLIGHT:
            self._projector.refresh_highlights()
        if task.kind in {TaskKind.PROXY, TaskKind.ANALYZE}:
            self._projector.schedule_preview_graph()
        if task.kind == TaskKind.WEB_RENDER:
            self._projector.schedule_preview_graph()
        self._projector.refresh_recent_projects()
        self._projector.refresh_tasks()
        self.workflowChanged.emit()

    def _active_workflow_run(self):
        return self._workflows.active_run() if self._workflows else None

    def _close_current(self, *, close_in_background: bool = True) -> None:
        self._session_generation += 1
        self._project_revision_timer.stop()
        if self._tasks and self._task_subscription_token is not None:
            self._tasks.events.unsubscribe(self._task_subscription_token)
        self._task_subscription_token = None
        closing_project = self._project
        self._project = None
        self._project_id = ""
        self._task_revisions = {}
        self._task_view = {}
        self._tasks = None
        self._workflows = None
        self._documents = None
        self._assets = None
        self._subtitle_editor = None
        self._subtitle_publication = None
        self._editor = None
        self._active_sequence_id = ""
        self._selected_asset_ids = []
        self._selected_clip_ids = []
        self._selected_document_id = ""
        self._selected_subtitle_segment_ids = []
        self._selected_subtitle_placement_id = ""
        self._selected_highlight_id = ""
        self._selected_audio_bus_id = ""
        self._selected_audio_effect_id = ""
        self._selected_transition_id = ""
        self._selected_marker_id = ""
        self._selected_range_id = ""
        self._range_in_frame = None
        self._download_plan = None
        self._download_entry_selection = set()
        self._download_entry_model.set_items([])
        self.downloadPlanChanged.emit()
        self._preview_timer.stop()
        self._preview_graph_path = ""
        self._hdr_preview_active = False
        self._preview_subtitles = []
        self._preview_subtitles_by_track = {}
        self._waveform_cache.clear()
        self._waveform_pending.clear()
        self._asset_thumbnail_paths.clear()
        self._asset_thumbnail_pending_request = None
        self._asset_thumbnail_refresh_requested = False
        self._audio_metrics = {}
        if self._pending_profile_asset_id:
            self._pending_profile_asset_id = ""
            self._pending_profile_label = ""
            self._pending_profile_placement = _TimelinePlacement()
            self.profileConfirmationChanged.emit()
        self._pending_asset_batch_ids = []
        self._pending_asset_batch_placement = _TimelinePlacement()
        self._pending_import_drop_tasks = {}
        self._pending_import_drop_batches = {}
        if self._pending_relink_asset_id:
            self._pending_relink_asset_id = ""
            self._pending_relink_path = ""
            self.relinkConfirmationChanged.emit()
        self._asset_model.set_items([])
        self._sequence_model.set_items([])
        self._track_model.set_items([])
        self._clip_model.set_items([])
        self._transition_model.set_items([])
        self._marker_model.set_items([])
        self._range_model.set_items([])
        self._task_model.set_items([])
        self._document_model.set_items([])
        self._segment_model.set_items([])
        self._subtitle_placement_model.set_items([])
        self._highlight_model.set_items([])
        self._audio_bus_model.set_items([])
        self._audio_effect_model.set_items([])
        self._audio_effect_parameter_model.set_items([])
        self.audioMetricsChanged.emit()
        self.workflowChanged.emit()
        self.previewGraphChanged.emit()
        if closing_project:
            self._dispose_project(
                closing_project,
                close_in_background=close_in_background,
            )

    def _dispose_project(
        self,
        project: EditorProject,
        *,
        close_in_background: bool,
    ) -> None:
        if close_in_background and not self._shutting_down:
            self._submit_background(
                "project_close",
                (self._session_generation, str(project.project_dir)),
                project.close,
                publish_result=False,
            )
        else:
            project.close()

    def _submit_background(
        self,
        kind: str,
        request_id: object,
        operation,
        *,
        executor: ThreadPoolExecutor | None = None,
        publish_result: bool = True,
    ) -> None:
        if self._shutting_down:
            return
        worker = executor or self._background_executor
        future = worker.submit(operation)
        if publish_result:
            future.add_done_callback(
                lambda completed: self._publish_background_result(
                    kind,
                    request_id,
                    completed,
                )
            )

    def _publish_background_result(
        self,
        kind: str,
        request_id: object,
        completed: Future,
    ) -> None:
        try:
            result = completed.result()
        except Exception as error:
            payload = (kind, request_id, None, error)
        else:
            payload = (kind, request_id, result, None)
        if not self._shutting_down:
            self._background_bridge.resultReceived.emit(payload)

    @Slot(object)
    def _on_background_result(self, payload: object) -> None:
        try:
            kind, request_id, result, error = payload
        except (TypeError, ValueError):
            return
        if kind == "recent_projects":
            if request_id != self._recent_request_id:
                return
            if error:
                self.errorOccurred.emit(f"读取最近项目失败：{error}")
            else:
                self._projector.apply_recent_projects(result)
            return
        if kind == "video_encoders":
            if request_id != self._encoder_request_id:
                return
            if error:
                self.errorOccurred.emit(f"检测编码器失败：{error}")
            else:
                self._video_encoder_options = list(result)
                self.settingsChanged.emit()
            return
        if kind == "download_plan":
            if request_id != self._download_analysis_request_id:
                return
            self._download_analysis_busy = False
            if error:
                self._download_plan = None
                self._download_entry_selection = set()
                self._projector.refresh_download_entries()
                self.downloadPlanChanged.emit()
                self.errorOccurred.emit(f"读取视频信息失败：{error}")
            else:
                self._set_download_plan(result)
                self.downloadPlanChanged.emit()
            return
        if kind == "waveform":
            self._waveform_pending.discard(request_id)
            generation, asset_id, path_value = request_id
            if generation != self._session_generation:
                return
            if error:
                logger.warning(
                    "Failed to preload waveform (asset=%s, path=%s): %s",
                    asset_id,
                    path_value,
                    error,
                )
                return
            modified, waveform = result
            self._waveform_cache[asset_id] = (path_value, modified, waveform)
            self.waveformDataChanged.emit(asset_id)
            return
        if kind == "asset_thumbnails":
            if request_id != self._asset_thumbnail_pending_request:
                return
            self._asset_thumbnail_pending_request = None
            generation, _thumbnail_request_id, _project_path = request_id
            if generation != self._session_generation:
                return
            if error:
                logger.warning("Failed to prepare asset thumbnails: %s", error)
            else:
                self._projector.apply_asset_thumbnails(result)
            if self._asset_thumbnail_refresh_requested:
                self._asset_thumbnail_refresh_requested = False
                assets = self._documents.list_assets() if self._documents else []
                self._projector.request_asset_thumbnails(assets)
            return
        if kind == "audio_metrics":
            generation, metrics_request_id, sequence_id = request_id
            if (
                generation != self._session_generation
                or metrics_request_id != self._audio_metrics_request_id
                or sequence_id != self._active_sequence_id
            ):
                return
            if error:
                logger.warning(
                    "Failed to read loudness metrics (sequence=%s): %s",
                    sequence_id,
                    error,
                )
                metrics = {}
            else:
                metrics = dict(result)
            if metrics != self._audio_metrics:
                self._audio_metrics = metrics
            self.audioMetricsChanged.emit()
            return
        if kind != "preview":
            return
        generation, preview_request_id, sequence_id = request_id
        if (
            generation != self._session_generation
            or preview_request_id != self._preview_request_id
            or sequence_id != self._active_sequence_id
        ):
            return
        if error:
            self.errorOccurred.emit(f"预览图编译失败：{error}")
            return
        self._preview_graph_path = str(result)
        self.previewGraphChanged.emit()
        if self._pending_preview_range is not None:
            start_frame, end_frame = self._pending_preview_range
            self._pending_preview_range = None
            self.previewRangeRequested.emit(start_frame, end_frame)

    @Slot(str)
    def _log_ui_error(self, message: str) -> None:
        error_id = uuid.uuid4().hex[:10]
        self._last_error_id = error_id
        self.errorReferenceChanged.emit()
        logger.error(
            "UI operation failed [%s]: %s",
            error_id,
            message,
            exc_info=sys.exception() is not None,
        )

    def _require_writable(self) -> None:
        if not self._documents or not self._editor or not self._tasks:
            raise RuntimeError("请先打开一个项目")
        if self._documents.read_only:
            raise PermissionError("项目以只读方式打开")

    def _require_subtitle_document(self) -> None:
        if not self._documents or not self._subtitle_editor or not self._selected_document_id:
            raise RuntimeError("请先选择字幕文档")
        self._documents.get_subtitle_document(self._selected_document_id)

    def _finish_subtitle_edit(self, selected_ids: list[str], status: str) -> None:
        self._selected_subtitle_segment_ids = list(selected_ids)
        self._projector.refresh_documents()
        self._projector.refresh_preview_subtitles()
        self.selectionChanged.emit()
        self.projectStateChanged.emit()
        self.historyChanged.emit()
        self._projector.schedule_preview_graph()
        self._set_status(status)

    def _finish_sequence_in_out_edit(self, status: str) -> None:
        self._projector.refresh_sequences()
        self._projector.refresh_timeline()
        self.projectStateChanged.emit()
        self.historyChanged.emit()
        self._set_status(status)

    def _commit_settings(self, candidate: GlobalSettings, status: str = "") -> None:
        self._api.replace_settings(candidate)
        self.settings = self._api.settings
        if self._project:
            self._project.update_settings(self.settings)
            self._workflows = self._project.workflows
            self.workflowChanged.emit()
        self._projector.refresh_settings_models()
        self.settingsChanged.emit()
        self.selectionChanged.emit()
        if status:
            self._set_status(status)

    def _remember_default_project_directory(self, directory: Path, status: str = "") -> None:
        selected = str(directory.expanduser().resolve())
        if self.settings.ui.default_project_directory == selected:
            return
        candidate = self.settings.model_copy(deep=True)
        candidate.ui.default_project_directory = selected
        self._commit_settings(candidate, status)

    def _set_download_plan(self, plan: DownloadPlan) -> None:
        self._download_plan = plan
        self._download_entry_selection = {entry.index for entry in plan.entries if entry.available}
        self._projector.refresh_download_entries()

    def _set_status(self, message: str) -> None:
        self._status_message = message
        self.statusChanged.emit()

    def _timeline_snap_targets(
        self,
        excluded_clip_ids: Iterable[str],
        playhead_frame: int,
        *,
        excluded_subtitle_placement_ids: Iterable[str] = (),
    ) -> list[int]:
        targets = [0, max(0, playhead_frame)]
        if not self._editor:
            return targets
        excluded = set(excluded_clip_ids)
        excluded_placements = set(excluded_subtitle_placement_ids)
        state = self._editor.state
        for clip in state.clips:
            if clip.id not in excluded:
                targets.extend([clip.timeline_start, clip.timeline_end])
        for track in state.tracks:
            if track.kind != TrackKind.SUBTITLE:
                continue
            for placement in self._documents.list_subtitle_placements(track.id):
                if placement.id not in excluded_placements:
                    targets.extend([placement.start_frame, placement.end_frame])
        targets.extend(marker.frame for marker in state.markers)
        for item in state.ranges:
            targets.extend([item.start_frame, item.end_frame])
        return targets

    def _start_download_workflow(
        self,
        requests: list[DownloadRequest],
    ) -> None:
        self._apply_workflow_update(self._workflows.begin_download(self._active_sequence_id, requests))

    def _apply_workflow_update(self, update: WorkflowUpdate) -> None:
        if update.selected_asset_ids:
            self._selected_asset_ids = list(update.selected_asset_ids)
            self.selectionChanged.emit()
        if update.status_message:
            self._set_status(update.status_message)
        self._projector.refresh_sequences()
        self.workflowChanged.emit()

    @staticmethod
    def _snap_tolerance_frames(pixels_per_frame: float) -> int:
        return max(1, round(8.0 / max(0.01, pixels_per_frame)))

    @staticmethod
    def _updated_selection(
        current_ids: list[str],
        item_id: str,
        *,
        toggle: bool,
    ) -> list[str]:
        if not item_id:
            return []
        if not toggle:
            return [item_id]
        if item_id in current_ids:
            return [value for value in current_ids if value != item_id]
        return [*current_ids, item_id]

    @staticmethod
    def _local_path(value: str) -> Path:
        url = QUrl(value)
        path = url.toLocalFile() if url.isLocalFile() else value
        return Path(path).expanduser().resolve()

    @staticmethod
    def _safe_project_name(name: str) -> str:
        invalid = '<>:"/\\|?*'
        sanitized = "".join("_" if character in invalid else character for character in name).strip(" .")
        if not sanitized:
            raise ValueError("项目名称无效")
        return sanitized
