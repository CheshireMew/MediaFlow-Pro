from __future__ import annotations

from fractions import Fraction

from PySide6.QtCore import Signal, Slot

from mediaflow.desktop.session_state import TimelinePlacement
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.timebase import source_frame_at_timeline_offset
from mediaflow.domain.timeline import ClipAudio, ClipTransform

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope
from .timeline_selection import selected_clip_id


class TimelineClipController(ControllerFacet[TimelinePresentationScope]):
    exclusiveSelectionRequested = Signal()
    errorOccurred = Signal(str)

    @Slot("QVariantList", str, int, int, float, int, bool, bool)
    @report_ui_errors
    def dropAssets(
        self,
        asset_ids: list[object],
        track_id: str,
        track_position: int,
        start_frame: int,
        pixels_per_frame: float,
        playhead_frame: int,
        snap_enabled: bool,
        force_new_track: bool,
    ) -> None:
        self._session._require_writable()
        self._session.timeline_assets.queue_for_timeline(
            [str(asset_id) for asset_id in asset_ids],
            TimelinePlacement(
                track_id=track_id,
                track_position=track_position if track_position >= 0 else None,
                start_frame=max(0, start_frame),
                pixels_per_frame=pixels_per_frame,
                playhead_frame=playhead_frame,
                snap_enabled=snap_enabled,
                force_new_track=force_new_track,
            ),
        )

    @Slot("QVariantList", str, int, int, float, int, bool, bool)
    @report_ui_errors
    def importFilesToTimeline(
        self,
        path_urls: list[object],
        track_id: str,
        track_position: int,
        start_frame: int,
        pixels_per_frame: float,
        playhead_frame: int,
        snap_enabled: bool,
        force_new_track: bool,
    ) -> None:
        self._session.timeline_assets.import_media_paths(
            path_urls,
            placement=TimelinePlacement(
                track_id=track_id,
                track_position=track_position if track_position >= 0 else None,
                start_frame=max(0, start_frame),
                pixels_per_frame=pixels_per_frame,
                playhead_frame=playhead_frame,
                snap_enabled=snap_enabled,
                force_new_track=force_new_track,
            ),
        )

    @Slot(str, int, int, bool, result="QVariantMap")
    def previewClipMove(
        self,
        clip_id: str,
        start_frame: int,
        requested_track_position: int,
        from_linked_audio: bool,
    ) -> dict:
        tracks = sorted(
            self._session.state.binding.require_timeline().state.tracks, key=lambda item: item.position
        )
        if not 0 <= requested_track_position < len(tracks):
            return {"accepted": False}
        requested = tracks[requested_track_position]
        if from_linked_audio:
            requested = next(
                (
                    track
                    for track in tracks
                    if track.kind == TrackKind.VIDEO and track.linked_audio_track_id == requested.id
                ),
                None,
            )
            if requested is None:
                return {"accepted": False}
        selected_ids = (
            self._session.state.selection.clip_ids
            if clip_id in self._session.state.selection.clip_ids
            else [clip_id]
        )
        try:
            self._session.state.binding.require_timeline().preview_move_clips(
                selected_ids,
                primary_clip_id=clip_id,
                timeline_start=max(0, start_frame),
                track_id=requested.id,
            )
        except (KeyError, PermissionError, ValueError):
            return {"accepted": False}
        positions = {track.id: position for position, track in enumerate(tracks)}
        audio_position = (
            positions.get(requested.linked_audio_track_id, -1) if requested.kind == TrackKind.VIDEO else -1
        )
        return {
            "accepted": True,
            "trackId": requested.id,
            "trackPosition": positions[requested.id],
            "audioTrackPosition": audio_position,
            "trackKind": requested.kind.value,
        }

    @Slot(str, int, str)
    @Slot(str, int, str, float, int, bool)
    @report_ui_errors
    def moveClip(
        self,
        clip_id: str,
        start_frame: int,
        track_id: str,
        pixels_per_frame: float = 3.0,
        playhead_frame: int = 0,
        snap_enabled: bool = True,
    ) -> None:
        self._session._require_writable()
        selected_ids = (
            self._session.state.selection.clip_ids
            if clip_id in self._session.state.selection.clip_ids
            else [clip_id]
        )
        targets = self._session._timeline_snap_targets(selected_ids, playhead_frame) if snap_enabled else []
        tolerance = self._session._snap_tolerance_frames(pixels_per_frame) if snap_enabled else 0
        snapped_start = self._session.state.binding.require_timeline().snap_frame(
            max(0, start_frame),
            targets,
            tolerance,
        )
        if len(selected_ids) > 1:
            source = next(
                item
                for item in self._session.state.binding.require_timeline().state.clips
                if item.id == clip_id
            )
            self._session.state.binding.require_timeline().move_clips(
                selected_ids,
                primary_clip_id=clip_id,
                timeline_start=snapped_start,
                track_id=track_id or source.track_id,
                snap_targets=(),
                snap_tolerance_frames=0,
            )
        else:
            self._session.state.binding.require_timeline().move_clip(
                clip_id,
                timeline_start=snapped_start,
                track_id=track_id or None,
                snap_targets=(),
                snap_tolerance_frames=0,
            )
        self._session.projectors.timeline.refresh_timeline(defer_clip_updates=True)
        self._session.updates.commit(history=True)

    @Slot(str, float, int)
    @report_ui_errors
    def duplicateClip(self, clip_id: str, pixels_per_frame: float, playhead_frame: int) -> None:
        self._session._require_writable()
        source = next(
            item for item in self._session.state.binding.require_timeline().state.clips if item.id == clip_id
        )
        copied = self._session.state.binding.require_timeline().copy_clip(
            clip_id,
            timeline_start=source.timeline_end,
            snap_targets=self._session._timeline_snap_targets([clip_id], playhead_frame),
            snap_tolerance_frames=self._session._snap_tolerance_frames(pixels_per_frame),
        )
        self._session.state.selection.clip_ids = [copied.id]
        self._session.state.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str, int)
    @report_ui_errors
    def splitClip(self, clip_id: str, frame: int) -> None:
        self._session._require_writable()
        _, right = self._session.state.binding.require_timeline().split_clip(clip_id, frame)
        self._session.state.selection.clip_ids = [right.id]
        self._session.state.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str)
    @report_ui_errors
    def detachClipAudio(self, clip_id: str) -> None:
        self._session._require_writable()
        video, _audio = self._session.state.binding.require_timeline().detach_clip_audio(clip_id)
        self._session.state.selection.clip_ids = [video.id]
        self._session.state.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.schedule_preview_graph()
        self.exclusiveSelectionRequested.emit()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        self._session._set_status("已解除视音频绑定；当前仅选中视频。点击空白处或按 Esc 可清除选择")

    @Slot(bool)
    @report_ui_errors
    def deleteSelectedClips(self, ripple: bool = False) -> None:
        self._session._require_writable()
        if not self._session.state.selection.clip_ids:
            return
        self._session.state.binding.require_timeline().delete_clips(
            self._session.state.selection.clip_ids, ripple=ripple
        )
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str, int, int)
    @report_ui_errors
    def trimClip(self, clip_id: str, source_in: int, duration: int) -> None:
        self._session._require_writable()
        clip = next(
            item for item in self._session.state.binding.require_timeline().state.clips if item.id == clip_id
        )
        self._session.state.binding.require_timeline().trim_clip(
            clip_id,
            timeline_start=clip.timeline_start,
            source_in=max(0, source_in),
            duration=max(1, duration),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str, int, int, bool)
    @report_ui_errors
    def trimClipEdges(
        self,
        clip_id: str,
        timeline_start: int,
        duration: int,
        trim_left: bool,
    ) -> None:
        self._session._require_writable()
        clip = next(
            item for item in self._session.state.binding.require_timeline().state.clips if item.id == clip_id
        )
        source_in = clip.source_in
        if trim_left:
            delta = timeline_start - clip.timeline_start
            source_in = source_frame_at_timeline_offset(
                clip.source_in,
                delta,
                clip.speed_numerator,
                clip.speed_denominator,
                freeze_source_frame=clip.freeze_source_frame,
            )
        self._session.state.binding.require_timeline().trim_clip(
            clip_id,
            timeline_start=max(0, timeline_start),
            source_in=max(0, source_in),
            duration=max(1, duration),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str, float, bool)
    @report_ui_errors
    def setClipSpeed(self, clip_id: str, speed: float, pitch_compensation: bool) -> None:
        self._session._require_writable()
        if abs(speed) < 0.25 or abs(speed) > 4.0:
            raise ValueError("速度必须在 0.25×～4× 或 -0.25×～-4×之间")
        fraction = Fraction(str(speed)).limit_denominator(1000)
        self._session.state.binding.require_timeline().set_clip_speed(
            clip_id,
            speed_numerator=fraction.numerator,
            speed_denominator=fraction.denominator,
            pitch_compensation=pitch_compensation,
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str, float, float, float, float, float, float, float, float, float, float)
    @report_ui_errors
    def setClipTransform(
        self,
        clip_id: str,
        x: float,
        y: float,
        scale_x: float,
        scale_y: float,
        rotation: float,
        crop_left: float,
        crop_top: float,
        crop_right: float,
        crop_bottom: float,
        opacity: float,
    ) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().set_clip_transform(
            clip_id,
            ClipTransform(
                x=x,
                y=y,
                scale_x=max(0.01, scale_x),
                scale_y=max(0.01, scale_y),
                rotation=rotation,
                crop_left=crop_left,
                crop_top=crop_top,
                crop_right=crop_right,
                crop_bottom=crop_bottom,
                opacity=opacity,
            ),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str, float, float, int, int)
    @report_ui_errors
    def setClipAudio(
        self,
        clip_id: str,
        gain_db: float,
        pan: float,
        fade_in_frames: int,
        fade_out_frames: int,
    ) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().set_clip_audio(
            clip_id,
            ClipAudio(
                gain_db=max(-60.0, min(24.0, gain_db)),
                pan=pan,
                fade_in_frames=max(0, fade_in_frames),
                fade_out_frames=max(0, fade_out_frames),
            ),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str)
    @report_ui_errors
    def replaceSelectedClipSource(self, asset_id: str) -> None:
        self._session._require_writable()
        if not selected_clip_id(self._session):
            raise ValueError("请先选择一个片段")
        self._session.state.binding.require_timeline().replace_clip_source(
            selected_clip_id(self._session),
            asset_id,
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        self._session._set_status("片段素材已替换")

    @Slot(float, float, int, int, float)
    @report_ui_errors
    def setSelectedClipsProperties(
        self,
        gain_db: float,
        pan: float,
        fade_in_frames: int,
        fade_out_frames: int,
        opacity: float,
    ) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().set_clips_properties(
            self._session.state.selection.clip_ids,
            gain_db=max(-60.0, min(24.0, gain_db)),
            pan=max(-1.0, min(1.0, pan)),
            fade_in_frames=max(0, fade_in_frames),
            fade_out_frames=max(0, fade_out_frames),
            opacity=max(0.0, min(1.0, opacity)),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
