from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Slot

from mediaflow.application.workflow_stage_handlers import WorkflowUpdate
from mediaflow.desktop.coordinators import (
    BackgroundRequests,
    ProjectLifecycle,
    RuntimeToolOperations,
    SettingsPersistence,
    TaskOperations,
    TimelineAssetOperations,
)
from mediaflow.desktop.presenters import PresentationProjectors
from mediaflow.desktop.session_events import SESSION_EVENT_NAMES, SessionEvents
from mediaflow.desktop.session_state import (
    AssetInteractionState,
    AsyncRequestState,
    DownloadState,
    PresentationState,
    ProjectBinding,
    RuntimeToolState,
    SelectionState,
    SessionModels,
    TaskViewState,
)
from mediaflow.domain.downloads import DownloadPlan, DownloadRequest
from mediaflow.domain.enums import (
    TrackKind,
)
from mediaflow.domain.sequence_audio import select_audible_sequence_audio
from mediaflow.service.client import EditorServiceRpcError
from mediaflow.service.desktop_proxy import (
    DesktopEditorApplication,
    create_desktop_editor_application,
)

from .session_helpers import (
    collaboration_conflict_details,
    snap_tolerance_frames,
    updated_selection,
)

logger = logging.getLogger(__name__)


class ProjectSession(QObject):
    def __init__(
        self,
        parent: QObject | None = None,
        *,
        application: DesktopEditorApplication | None = None,
    ):
        super().__init__(parent)
        self._api = application or create_desktop_editor_application()
        self.service_settings = self._api.service_settings
        self.desktop_settings = self._api.desktop_settings
        self.binding = ProjectBinding()
        self.selection = SelectionState()
        self.task_state = TaskViewState()
        self.presentation = PresentationState()
        self.asset_state = AssetInteractionState()
        self.download_state = DownloadState()
        self.runtime_state = RuntimeToolState(
            status={
                **self._api.runtime_tool_status(),
                "busy": False,
                "progressMode": "indeterminate",
                "progressValue": 0.0,
                "message": "",
                "operation": "",
            }
        )
        self.requests = AsyncRequestState()
        self.models = SessionModels.create(self)
        self.events = SessionEvents(self)
        self.background = BackgroundRequests(self)
        self.runtime_tools = RuntimeToolOperations(self)
        self.settings_persistence = SettingsPersistence(self)
        self.tasks = TaskOperations(self)
        self.timeline_assets = TimelineAssetOperations(self)
        self.projectors = PresentationProjectors.create(self)
        self.lifecycle = ProjectLifecycle(self)
        self._controller_notifiers_attached = False
        self.events.errorOccurred.connect(self._log_ui_error)
        self.projectors.workspace.refresh_settings_models()
        self.projectors.workspace.refresh_recent_projects()
        self.projectors.workspace.discover_encoder_policies()

    def _attach_controllers(self, controllers: dict[str, QObject]) -> None:
        if self._controller_notifiers_attached:
            raise RuntimeError("Project controllers are already attached")
        self._controller_notifiers_attached = True
        for signal_name in SESSION_EVENT_NAMES:
            event = getattr(self.events, signal_name)
            for controller in controllers.values():
                if not hasattr(type(controller), signal_name):
                    continue
                event.connect(getattr(controller, signal_name).emit)

    @Slot(str)
    def _log_ui_error(self, message: str) -> None:
        error_id = uuid.uuid4().hex[:10]
        self.presentation.last_error_id = error_id
        self.events.errorReferenceChanged.emit()
        logger.error(
            "UI operation failed [%s]: %s",
            error_id,
            message,
            exc_info=sys.exception() is not None,
        )

    def _present_collaboration_conflict(
        self,
        error: EditorServiceRpcError,
    ) -> None:
        self.presentation.collaboration_conflict = collaboration_conflict_details(error)
        self.events.collaborationConflictChanged.emit()

    def _require_writable(self) -> None:
        if not self.binding.current or not self.binding.timeline:
            raise RuntimeError("请先打开一个项目")
        if self.binding.current.read_only:
            raise PermissionError("项目以只读方式打开")

    def _active_sequence_has_renderable_content(self) -> bool:
        if not self.binding.current or not self.binding.timeline:
            return False
        state = self.binding.timeline.state
        active_video_track_ids = {
            track.id for track in state.effective_tracks(TrackKind.VIDEO)
        }
        if any(clip.track_id in active_video_track_ids for clip in state.clips):
            return True
        assets = {
            asset.id: asset for asset in self.binding.current.list_assets()
        }
        audio = select_audible_sequence_audio(
            state,
            assets,
            self.binding.current.list_audio_buses(state.sequence.id),
        )
        return bool(audio.asset_ids)

    def _require_exportable_sequence(self) -> None:
        self._require_writable()
        if not self._active_sequence_has_renderable_content():
            raise ValueError("当前序列没有可导出的媒体片段")

    def _require_subtitle_document(self) -> None:
        if not self.binding.current or not self.selection.document_id:
            raise RuntimeError("请先选择字幕文档")
        self.binding.current.get_subtitle_document(self.selection.document_id)

    def _finish_subtitle_edit(self, selected_ids: list[str], status: str) -> None:
        self.selection.subtitle_segment_ids = list(selected_ids)
        self.projectors.subtitles.refresh_documents()
        self.projectors.timeline.refresh_preview_subtitles()
        self.events.selectionChanged.emit()
        self.events.projectStateChanged.emit()
        self.events.historyChanged.emit()
        self.projectors.timeline.schedule_preview_graph()
        self._set_status(status)

    def _finish_sequence_in_out_edit(self, status: str) -> None:
        self.projectors.timeline.refresh_sequences()
        self.projectors.timeline.refresh_timeline()
        self.events.projectStateChanged.emit()
        self.events.historyChanged.emit()
        self._set_status(status)

    def _set_download_plan(self, plan: DownloadPlan) -> None:
        self.download_state.plan = plan
        self.download_state.selected_entries = {entry.index for entry in plan.entries if entry.available}
        self.projectors.tasks.refresh_download_entries()
        self.events.downloadPlanChanged.emit()

    def _set_status(self, message: str) -> None:
        self.presentation.status_message = message
        self.events.statusChanged.emit()

    def _timeline_snap_targets(
        self,
        excluded_clip_ids: Iterable[str],
        playhead_frame: int,
        *,
        excluded_subtitle_placement_ids: Iterable[str] = (),
    ) -> list[int]:
        targets = [0, max(0, playhead_frame)]
        if not self.binding.timeline:
            return targets
        excluded = set(excluded_clip_ids)
        excluded_placements = set(excluded_subtitle_placement_ids)
        state = self.binding.timeline.state
        for clip in state.clips:
            if clip.id not in excluded:
                targets.extend([clip.timeline_start, clip.timeline_end])
        subtitle_track_ids = {track.id for track in state.tracks if track.kind == TrackKind.SUBTITLE}
        for placement in self.models.subtitle_placements.snapshot():
            if (
                placement["trackId"] in subtitle_track_ids
                and placement["placementId"] not in excluded_placements
            ):
                targets.extend((int(placement["startFrame"]), int(placement["endFrame"])))
        targets.extend(marker.frame for marker in state.markers)
        for item in state.ranges:
            targets.extend([item.start_frame, item.end_frame])
        return targets

    def _start_download_workflow(
        self,
        requests: list[DownloadRequest],
    ) -> None:
        self._apply_workflow_update(
            self.binding.current.begin_download_workflow(self.binding.active_sequence_id, requests)
        )

    def _apply_workflow_update(self, update: WorkflowUpdate) -> None:
        if update.selected_asset_ids:
            self.selection.asset_ids = list(update.selected_asset_ids)
            self.events.selectionChanged.emit()
        if update.status_message:
            self._set_status(update.status_message)
        self.projectors.timeline.refresh_sequences()
        self.events.workflowChanged.emit()

    _snap_tolerance_frames = staticmethod(snap_tolerance_frames)
    _updated_selection = staticmethod(updated_selection)
    @staticmethod
    def _local_path(value: str) -> Path:
        url = QUrl(value)
        path = url.toLocalFile() if url.isLocalFile() else value
        return Path(path).expanduser().resolve()
