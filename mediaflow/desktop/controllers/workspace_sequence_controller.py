from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Slot

from mediaflow.desktop.session_state import TimelinePlacement
from mediaflow.domain.enums import ColorMode
from mediaflow.domain.project import ProjectProfile

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import WorkspaceSequenceScope


class WorkspaceSequenceController(ControllerFacet[WorkspaceSequenceScope]):
    """Active-sequence selection, profile, preview, and placement commands."""

    @Slot(str)
    @report_ui_errors
    def selectSequence(self, sequence_id: str) -> None:
        if not self._session.state.binding.current:
            return
        self._session.state.binding.require_current().get_sequence(sequence_id)
        self._session.state.binding.active_sequence_id = sequence_id
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            sequence_id
        )
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.projectors.refresh_active_sequence()

    @Slot(str)
    @report_ui_errors
    def createShortSequence(self, name: str) -> None:
        self._session._require_writable()
        selected_name = name.strip()
        if not selected_name:
            existing_names = {
                item.name
                for item in self._session.state.binding.require_current().list_sequences(
                    include_archived=True
                )
            }
            sequence_number = 1
            while f"短视频 {sequence_number}" in existing_names:
                sequence_number += 1
            selected_name = f"短视频 {sequence_number}"
        sequence = self._session.state.binding.require_current().create_short_sequence(selected_name)
        self._session.state.binding.active_sequence_id = sequence.id
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            sequence.id
        )
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("短视频序列已创建")

    @Slot()
    @report_ui_errors
    def archiveActiveSequence(self) -> None:
        self._session._require_writable()
        project = self._session.state.binding.require_current().get_project()
        sequence_id = self._session.state.binding.active_sequence_id
        if sequence_id == project.main_sequence_id:
            raise ValueError("主序列不能删除")
        self._session.state.binding.require_current().archive_short_sequence(sequence_id)
        self._session.state.binding.active_sequence_id = project.main_sequence_id
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            project.main_sequence_id
        )
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("短视频序列已移除；可使用撤销恢复")

    @Slot(bool)
    def resolveProfileAdoption(self, adopt: bool) -> None:
        asset_id = self._session.state.assets.pending_profile_asset_id
        placement = self._session.state.assets.pending_profile_placement
        self._session.state.assets.pending_profile_asset_id = ""
        self._session.state.assets.pending_profile_label = ""
        self._session.state.assets.pending_profile_placement = TimelinePlacement()
        self._session.updates.commit(profile_confirmation=True)
        if not asset_id:
            return
        try:
            self._session._require_writable()
            if adopt:
                self._session.state.binding.require_current().adopt_main_profile_from_video(asset_id)
                self._session.state.binding.require_timeline().reload()
                self._session.projectors.timeline.refresh_sequences()
                self._session.updates.commit(project=True)
            placed = self._session.timeline_assets.place_on_timeline(
                self._session.state.binding.require_current().get_asset(asset_id),
                placement,
            )
            if self._session.state.assets.pending_batch_placement.start_frame is not None:
                self._session.state.assets.pending_batch_placement = replace(
                    self._session.state.assets.pending_batch_placement,
                    track_id=placed.track_id,
                    start_frame=placed.end_frame,
                    force_new_track=False,
                )
            self._session.timeline_assets.continue_batch()
        except Exception as error:
            self._session.state.assets.pending_batch_ids = []
            self._session.updates.report_error(str(error))

    @Slot(int)
    def reportPreviewDroppedFrames(self, dropped_frames: int) -> None:
        if (
            dropped_frames < self._session.state.service_settings.preview.dropped_frame_proxy_threshold
            or not self._session.state.binding.current
            or not self._session.state.binding.timeline
            or self._session.state.binding.require_current().read_only
        ):
            return
        asset_ids = {clip.asset_id for clip in self._session.state.binding.require_timeline().state.clips}
        for asset_id in asset_ids:
            asset = self._session.state.binding.require_current().get_asset(asset_id)
            if not asset.proxy_path:
                self._session.timeline_assets.schedule_background(asset, dropped_frames=dropped_frames)

    @Slot(bool)
    def reportHdrPreviewActive(self, active: bool) -> None:
        if self._session.state.presentation.hdr_preview_active == active:
            return
        self._session.state.presentation.hdr_preview_active = active
        self._session.projectors.timeline.schedule_preview_graph()

    @Slot(int, int, int, int, str, int)
    @report_ui_errors
    def updateSequenceProfile(
        self,
        width: int,
        height: int,
        fps_numerator: int,
        fps_denominator: int,
        color_mode: str,
        audio_channels: int,
    ) -> None:
        self._session._require_writable()
        mode = ColorMode(color_mode)
        self._session.state.binding.require_timeline().set_sequence_profile(
            ProjectProfile(
                width=width,
                height=height,
                fps_numerator=fps_numerator,
                fps_denominator=fps_denominator,
                color_mode=mode,
                bit_depth=10 if mode == ColorMode.HDR10_BT2020_PQ else 8,
                audio_channels=audio_channels,
            )
        )
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.refresh_sequences()
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(project=True, history=True)
        self._session._set_status("序列配置已更新")

    @Slot()
    def saveProject(self) -> None:
        if self._session.state.binding.current:
            self._session._set_status("项目已保存")
