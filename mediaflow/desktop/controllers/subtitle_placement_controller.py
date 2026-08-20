from __future__ import annotations

from PySide6.QtCore import Slot

from mediaflow.domain.enums import TrackKind

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import SubtitlePresentationScope
from .subtitle_selection import select_subtitle_placement_context


class SubtitlePlacementController(ControllerFacet[SubtitlePresentationScope]):
    @Slot(str, int, float, int, bool)
    @report_ui_errors
    def moveSubtitlePlacement(
        self,
        placement_id: str,
        start_frame: int,
        pixels_per_frame: float,
        playhead_frame: int,
        snap_enabled: bool,
    ) -> None:
        self._session._require_writable()
        placement = self._session.state.binding.require_current().get_subtitle_placement(placement_id)
        self._require_unlocked_subtitle_track(placement.track_id)
        duration = placement.end_frame - placement.start_frame
        next_start = max(0, int(start_frame))
        if snap_enabled:
            targets = self._session._timeline_snap_targets(
                [],
                playhead_frame,
                excluded_subtitle_placement_ids=[placement_id],
            )
            tolerance = self._session._snap_tolerance_frames(pixels_per_frame)
            adjustments = [
                target - edge
                for target in targets
                for edge in (next_start, next_start + duration)
                if abs(target - edge) <= tolerance
            ]
            if adjustments:
                next_start = max(0, next_start + min(adjustments, key=lambda value: abs(value)))
        self._session.state.binding.require_current().update_subtitle_placement_range(
            placement_id,
            start_frame=next_start,
            end_frame=next_start + duration,
        )
        self._finish_subtitle_placement_edit(placement_id, "已移动序列字幕")

    @Slot(str, int, int, float, int, bool)
    @report_ui_errors
    def resizeSubtitlePlacement(
        self,
        placement_id: str,
        start_frame: int,
        end_frame: int,
        pixels_per_frame: float,
        playhead_frame: int,
        snap_enabled: bool,
    ) -> None:
        self._session._require_writable()
        placement = self._session.state.binding.require_current().get_subtitle_placement(placement_id)
        self._require_unlocked_subtitle_track(placement.track_id)
        next_start = max(0, int(start_frame))
        next_end = max(next_start + 1, int(end_frame))
        if snap_enabled:
            targets = self._session._timeline_snap_targets(
                [],
                playhead_frame,
                excluded_subtitle_placement_ids=[placement_id],
            )
            tolerance = self._session._snap_tolerance_frames(pixels_per_frame)
            if next_start != placement.start_frame:
                next_start = self._session.state.binding.require_timeline().snap_frame(
                    next_start, targets, tolerance
                )
                next_start = max(0, min(next_start, next_end - 1))
            if next_end != placement.end_frame:
                next_end = self._session.state.binding.require_timeline().snap_frame(
                    next_end, targets, tolerance
                )
                next_end = max(next_start + 1, next_end)
        self._session.state.binding.require_current().update_subtitle_placement_range(
            placement_id,
            start_frame=next_start,
            end_frame=next_end,
        )
        self._finish_subtitle_placement_edit(placement_id, "已调整序列字幕时间")

    @Slot(str)
    @report_ui_errors
    def resetSubtitlePlacementTiming(self, placement_id: str) -> None:
        self._session._require_writable()
        placement = self._session.state.binding.require_current().get_subtitle_placement(placement_id)
        self._require_unlocked_subtitle_track(placement.track_id)
        self._session.state.binding.require_current().reset_subtitle_placement_range(placement_id)
        self._finish_subtitle_placement_edit(placement_id, "已恢复字幕文档时间")

    @Slot(str)
    @report_ui_errors
    def placeSubtitleDocument(self, document_id: str) -> None:
        self._session._require_writable()
        subtitle_track = next(
            (
                track
                for track in self._session.state.binding.require_timeline().state.tracks
                if track.kind == TrackKind.SUBTITLE and not track.locked
            ),
            None,
        )
        if subtitle_track is None:
            subtitle_track = self._session.state.binding.require_timeline().add_track(TrackKind.SUBTITLE)
        document = self._session.state.binding.require_current().get_subtitle_document(document_id)
        media_asset_id = document.media_asset_id or document.asset_id
        matching_clips = [
            clip
            for clip in self._session.state.binding.require_timeline().state.clips
            if clip.asset_id == media_asset_id
        ]
        if matching_clips:
            placements = self._session.state.binding.require_current().place_subtitle_document(
                document_id,
                subtitle_track.id,
                follow_clips=True,
            )
        else:
            placements = self._session.state.binding.require_current().place_subtitle_document(
                document_id, subtitle_track.id
            )
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.updates.commit(project=True)
        self._session._set_status("已放入 %1 条字幕", len(placements))

    @Slot(str, str, bool)
    @report_ui_errors
    def updateSubtitlePlacementText(
        self,
        placement_id: str,
        text: str,
        apply_to_document: bool,
    ) -> None:
        self._session._require_writable()
        if apply_to_document:
            self._session.state.binding.require_current().apply_subtitle_placement_to_document(
                placement_id, text
            )
            self._session.projectors.subtitles.refresh_documents()
            self._session._set_status("修改已应用到字幕文档")
        else:
            self._session.state.binding.require_current().update_subtitle_placement_text(placement_id, text)
            self._session._set_status("已保存序列字幕覆盖")
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.updates.commit(selection=True)
        self._session.projectors.timeline.schedule_preview_graph()

    def _require_unlocked_subtitle_track(self, track_id: str) -> None:
        track = next(
            (
                item
                for item in self._session.state.binding.require_timeline().state.tracks
                if item.id == track_id
            ),
            None,
        )
        if track is None:
            raise KeyError(track_id)
        if track.locked:
            raise ValueError("字幕轨道已锁定")

    def _finish_subtitle_placement_edit(
        self,
        placement_id: str,
        status_source: str,
        *status_arguments: object,
    ) -> None:
        self._session.state.selection.subtitle_placement_id = placement_id
        self._session.projectors.timeline.refresh_preview_subtitles()
        select_subtitle_placement_context(self._session, placement_id)
        self._session.projectors.timeline.schedule_preview_graph()
        self._session._set_status(status_source, *status_arguments)
        self._session.updates.commit(history=True)
