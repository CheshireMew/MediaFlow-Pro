from __future__ import annotations

import logging
import sys
import time
import uuid
from collections.abc import Iterable
from datetime import datetime
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
from mediaflow.desktop.presentation_messages import status_message
from mediaflow.desktop.presenters import PresentationProjectors
from mediaflow.desktop.session_events import SESSION_EVENT_NAMES, SessionEvents
from mediaflow.desktop.session_state import (
    DesktopSessionState,
    RuntimeToolState,
    SessionModels,
)
from mediaflow.desktop.session_updates import SessionUpdates
from mediaflow.domain.downloads import DownloadPlan, DownloadRequest
from mediaflow.domain.enums import (
    TrackKind,
)
from mediaflow.domain.sequence_audio import select_audible_sequence_audio
from mediaflow.service.client import EditorServiceRpcError
from mediaflow.service.desktop_application_proxy import (
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
        self.state = DesktopSessionState(
            service_settings=self._api.service_settings,
            desktop_settings=self._api.desktop_settings,
            runtime_state=RuntimeToolState(
                status={
                    **self._api.runtime_tool_status(),
                    "busy": False,
                    "progressMode": "indeterminate",
                    "progressValue": 0.0,
                    "message": "",
                    "operation": "",
                }
            ),
        )
        self.models = SessionModels.create(self)
        self.events = SessionEvents(self)
        self.updates = SessionUpdates(self.events, self)
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
        self.state.presentation.last_error_id = error_id
        self.state.presentation.recent_errors.insert(
            0,
            {
                "errorId": error_id,
                "message": message,
                "timestamp": time.time(),
                "timeLabel": datetime.now().astimezone().strftime("%H:%M:%S"),
            },
        )
        del self.state.presentation.recent_errors[50:]
        self.updates.commit(error_reference=True, error_history=True)
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
        self.state.presentation.collaboration_conflict = collaboration_conflict_details(error)
        self.updates.commit(collaboration_conflict=True)

    def _require_writable(self) -> None:
        if not self.state.binding.current or not self.state.binding.timeline:
            raise RuntimeError("请先打开一个项目")
        if self.state.binding.require_current().read_only:
            raise PermissionError("项目以只读方式打开")

    def _active_sequence_has_renderable_content(self) -> bool:
        if not self.state.binding.current or not self.state.binding.timeline:
            return False
        state = self.state.binding.require_timeline().state
        active_video_track_ids = {track.id for track in state.effective_tracks(TrackKind.VIDEO)}
        if any(clip.track_id in active_video_track_ids for clip in state.clips):
            return True
        assets = {asset.id: asset for asset in self.state.binding.require_current().list_assets()}
        audio = select_audible_sequence_audio(
            state,
            assets,
            self.state.binding.require_current().list_audio_buses(state.sequence.id),
        )
        return bool(audio.asset_ids)

    def _require_exportable_sequence(self) -> None:
        self._require_writable()
        if not self._active_sequence_has_renderable_content():
            raise ValueError("当前序列没有可导出的媒体片段")

    def _require_subtitle_document(self) -> None:
        if not self.state.binding.current or not self.state.selection.document_id:
            raise RuntimeError("请先选择字幕文档")
        self.state.binding.require_current().get_subtitle_document(self.state.selection.document_id)

    def _finish_subtitle_edit(
        self,
        selected_ids: list[str],
        status_source: str,
        *status_arguments: object,
    ) -> None:
        self.state.selection.subtitle_segment_ids = list(selected_ids)
        self.projectors.subtitles.refresh_documents()
        self.projectors.timeline.refresh_preview_subtitles()
        self.updates.commit(selection=True)
        self.updates.commit(project=True)
        self.updates.commit(history=True)
        self.projectors.timeline.schedule_preview_graph()
        self._set_status(status_source, *status_arguments)

    def _finish_sequence_in_out_edit(
        self,
        status_source: str,
        *status_arguments: object,
    ) -> None:
        self.projectors.timeline.refresh_sequences()
        self.projectors.timeline.refresh_timeline()
        self.updates.commit(project=True)
        self.updates.commit(history=True)
        self._set_status(status_source, *status_arguments)

    def _set_download_plan(self, plan: DownloadPlan) -> None:
        self.state.download.plan = plan
        self.state.download.selected_entries = {entry.index for entry in plan.entries if entry.available}
        self.projectors.tasks.refresh_download_entries()
        self.updates.commit(download_plan=True)

    def _set_status(self, source: str, *arguments: object) -> None:
        self.state.presentation.status_message = status_message(source, *arguments)
        self.updates.commit(status=True)

    def _timeline_snap_targets(
        self,
        excluded_clip_ids: Iterable[str],
        playhead_frame: int,
        *,
        excluded_subtitle_placement_ids: Iterable[str] = (),
    ) -> list[int]:
        targets = [0, max(0, playhead_frame)]
        if not self.state.binding.timeline:
            return targets
        excluded = set(excluded_clip_ids)
        excluded_placements = set(excluded_subtitle_placement_ids)
        state = self.state.binding.require_timeline().state
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
        if self.state.binding.current is None:
            raise RuntimeError("请先打开一个项目")
        self._apply_workflow_update(
            self.state.binding.require_current().begin_download_workflow(
                self.state.binding.active_sequence_id, requests
            )
        )

    def _apply_workflow_update(self, update: WorkflowUpdate) -> None:
        if update.selected_asset_ids:
            self.state.selection.asset_ids = list(update.selected_asset_ids)
            self.updates.commit(selection=True)
        if update.status_source:
            self._set_status(update.status_source, *update.status_arguments)
        self.projectors.timeline.refresh_sequences()
        self.updates.commit(workflow=True)

    _snap_tolerance_frames = staticmethod(snap_tolerance_frames)
    _updated_selection = staticmethod(updated_selection)

    @staticmethod
    def _local_path(value: str) -> Path:
        url = QUrl(value)
        path = url.toLocalFile() if url.isLocalFile() else value
        return Path(path).expanduser().resolve()
