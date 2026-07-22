from __future__ import annotations

from fractions import Fraction

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.desktop.presentation_catalogs import (
    transition_options,
)
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import (
    ColorMode,
    TrackKind,
    TransitionKind,
)
from mediaflow.domain.task_commands import (
    AnalyzeSequenceBoundsCommand,
)
from mediaflow.domain.timebase import (
    source_frames_for_timeline_frames,
)
from mediaflow.domain.timeline import ClipAudio, ClipTransform

from .controller_facet import ControllerFacet
from .project_controller import _TimelinePlacement


class TimelineController(ControllerFacet):
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

    @Property(QObject, constant=True)
    def tracksModel(self) -> QObject:
        return self._track_model

    @Property(QObject, constant=True)
    def clipsModel(self) -> QObject:
        return self._clip_model

    @Property(QObject, constant=True)
    def transitionsModel(self) -> QObject:
        return self._transition_model

    @Property(QObject, constant=True)
    def timelineMarkersModel(self) -> QObject:
        return self._marker_model

    @Property(QObject, constant=True)
    def timelineRangesModel(self) -> QObject:
        return self._range_model

    @Property("QVariantList", notify=projectStateChanged)
    def transitionOptions(self) -> list[dict]:
        color_mode = self._editor.state.sequence.profile.color_mode if self._editor else ColorMode.SDR_BT709
        return transition_options(color_mode)

    @Slot(str, result=bool)
    def isTransitionAvailable(self, kind: str) -> bool:
        if not self._editor:
            return False
        try:
            return transition_is_available(
                TransitionKind(kind),
                self._editor.state.sequence.profile.color_mode,
            )
        except ValueError:
            return False

    @Property(bool, notify=tasksChanged)
    def sequenceBoundaryAnalysisRunning(self) -> bool:
        if not self._tasks or not self._active_sequence_id:
            return False
        return any(
            isinstance(task.command, AnalyzeSequenceBoundsCommand)
            and task.command.sequence_id == self._active_sequence_id
            and task.status.is_active
            for task in self._task_view.values()
        )

    @Property(str, notify=selectionChanged)
    def selectedClipId(self) -> str:
        return self._selected_clip_ids[-1] if self._selected_clip_ids else ""

    @Property("QVariantList", notify=selectionChanged)
    def selectedClipIds(self) -> list[str]:
        return list(self._selected_clip_ids)

    @Property(str, notify=selectionChanged)
    def selectedTransitionId(self) -> str:
        return self._selected_transition_id

    @Property(str, notify=selectionChanged)
    def selectedMarkerId(self) -> str:
        return self._selected_marker_id

    @Property(str, notify=selectionChanged)
    def selectedRangeId(self) -> str:
        return self._selected_range_id

    @Property(int, notify=selectionChanged)
    def rangeInFrame(self) -> int:
        return -1 if self._range_in_frame is None else self._range_in_frame

    @Property("QVariantMap", notify=selectionChanged)
    def selectedTransitionData(self) -> dict:
        row = self._transition_model.findRow("transitionId", self._selected_transition_id)
        return self._transition_model.get(row)

    @Property("QVariantMap", notify=selectionChanged)
    def selectedClipData(self) -> dict:
        row = self._clip_model.findRow("clipId", self.selectedClipId)
        return self._clip_model.get(row)

    @Property(bool, notify=historyChanged)
    def canUndo(self) -> bool:
        return bool(self._editor and self._editor.can_undo)

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:
        return bool(self._editor and self._editor.can_redo)

    @Slot(str)
    @Slot(str, bool)
    def selectClip(self, clip_id: str, toggle: bool = False) -> None:
        self._selected_clip_ids = self._updated_selection(
            self._selected_clip_ids,
            clip_id,
            toggle=toggle,
        )
        self._selected_transition_id = ""
        self.selectionChanged.emit()

    @Slot(str, result=bool)
    def isClipSelected(self, clip_id: str) -> bool:
        return clip_id in self._selected_clip_ids

    @Slot()
    def selectAllClips(self) -> None:
        self._selected_clip_ids = [clip.id for clip in self._editor.state.clips] if self._editor else []
        self._selected_transition_id = ""
        self._selected_marker_id = ""
        self._selected_range_id = ""
        self.selectionChanged.emit()

    @Slot()
    def clearSelection(self) -> None:
        self._selected_clip_ids = []
        self._selected_transition_id = ""
        self._selected_marker_id = ""
        self._selected_range_id = ""
        self.selectionChanged.emit()

    @Slot(str)
    def addTrack(self, kind: str) -> None:
        try:
            self._require_writable()
            track_kind = TrackKind(kind)
            self._add_timeline_track(track_kind)
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot("QVariantList", str, int, float, int, bool, bool)
    def dropAssets(
        self,
        asset_ids: list[object],
        track_id: str,
        start_frame: int,
        pixels_per_frame: float,
        playhead_frame: int,
        snap_enabled: bool,
        force_new_track: bool,
    ) -> None:
        try:
            self._require_writable()
            self._queue_assets_for_timeline(
                [str(asset_id) for asset_id in asset_ids],
                _TimelinePlacement(
                    track_id=track_id,
                    start_frame=max(0, start_frame),
                    pixels_per_frame=pixels_per_frame,
                    playhead_frame=playhead_frame,
                    snap_enabled=snap_enabled,
                    force_new_track=force_new_track,
                ),
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot("QVariantList", str, int, float, int, bool, bool)
    def importFilesToTimeline(
        self,
        path_urls: list[object],
        track_id: str,
        start_frame: int,
        pixels_per_frame: float,
        playhead_frame: int,
        snap_enabled: bool,
        force_new_track: bool,
    ) -> None:
        try:
            self._import_media_paths(
                path_urls,
                placement=_TimelinePlacement(
                    track_id=track_id,
                    start_frame=max(0, start_frame),
                    pixels_per_frame=pixels_per_frame,
                    playhead_frame=playhead_frame,
                    snap_enabled=snap_enabled,
                    force_new_track=force_new_track,
                ),
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, bool, bool, bool, bool, str)
    def updateTrack(
        self,
        track_id: str,
        enabled: bool,
        locked: bool,
        muted: bool,
        solo: bool,
        audio_bus_id: str,
    ) -> None:
        try:
            self._require_writable()
            self._editor.set_track_state(
                track_id,
                enabled=enabled,
                locked=locked,
                muted=muted,
                solo=solo,
                audio_bus_id=audio_bus_id or None,
            )
            self._projector.refresh_timeline()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int)
    def moveTrack(self, track_id: str, position: int) -> None:
        try:
            self._require_writable()
            self._editor.move_track(track_id, position)
            self._projector.refresh_timeline()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int, str)
    @Slot(str, int, str, float, int, bool)
    def moveClip(
        self,
        clip_id: str,
        start_frame: int,
        track_id: str,
        pixels_per_frame: float = 3.0,
        playhead_frame: int = 0,
        snap_enabled: bool = True,
    ) -> None:
        try:
            self._require_writable()
            selected_ids = self._selected_clip_ids if clip_id in self._selected_clip_ids else [clip_id]
            targets = self._timeline_snap_targets(selected_ids, playhead_frame) if snap_enabled else []
            tolerance = self._snap_tolerance_frames(pixels_per_frame) if snap_enabled else 0
            if len(selected_ids) > 1:
                source = next(item for item in self._editor.state.clips if item.id == clip_id)
                self._editor.move_clips(
                    selected_ids,
                    primary_clip_id=clip_id,
                    timeline_start=max(0, start_frame),
                    track_id=track_id or source.track_id,
                    snap_targets=targets,
                    snap_tolerance_frames=tolerance,
                )
            else:
                self._editor.move_clip(
                    clip_id,
                    timeline_start=max(0, start_frame),
                    track_id=track_id or None,
                    snap_targets=targets,
                    snap_tolerance_frames=tolerance,
                    transition_from_overlap=True,
                )
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, int)
    def duplicateClip(self, clip_id: str, pixels_per_frame: float, playhead_frame: int) -> None:
        try:
            self._require_writable()
            source = next(item for item in self._editor.state.clips if item.id == clip_id)
            copied = self._editor.copy_clip(
                clip_id,
                timeline_start=source.timeline_end,
                snap_targets=self._timeline_snap_targets([clip_id], playhead_frame),
                snap_tolerance_frames=self._snap_tolerance_frames(pixels_per_frame),
            )
            self._selected_clip_ids = [copied.id]
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int)
    def splitClip(self, clip_id: str, frame: int) -> None:
        try:
            self._require_writable()
            _, right = self._editor.split_clip(clip_id, frame)
            self._selected_clip_ids = [right.id]
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, bool)
    def deleteClip(self, clip_id: str, ripple: bool = False) -> None:
        try:
            self._require_writable()
            self._editor.delete_clip(clip_id, ripple=ripple)
            self._selected_clip_ids = [value for value in self._selected_clip_ids if value != clip_id]
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(bool)
    def deleteSelectedClips(self, ripple: bool = False) -> None:
        try:
            self._require_writable()
            if not self._selected_clip_ids:
                return
            self._editor.delete_clips(self._selected_clip_ids, ripple=ripple)
            self._selected_clip_ids = []
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str, int)
    def addTransitionAfter(self, clip_id: str, kind: str, duration: int) -> None:
        try:
            self._require_writable()
            state = self._editor.state
            left = next(clip for clip in state.clips if clip.id == clip_id)
            right = next(
                clip
                for clip in state.clips_for_track(left.track_id)
                if clip.timeline_start == left.timeline_end
            )
            transition = self._editor.create_transition(
                left.id,
                right.id,
                TransitionKind(kind),
                max(1, duration),
            )
            self._selected_transition_id = transition.id
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
            self._set_status("转场已添加")
        except StopIteration:
            self.errorOccurred.emit("所选片段后没有同轨道的相邻片段")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def selectTransition(self, transition_id: str) -> None:
        self._selected_transition_id = transition_id
        self._selected_clip_ids = []
        self.selectionChanged.emit()

    @Slot(str)
    def selectTimelineRange(self, range_id: str) -> None:
        self._selected_range_id = range_id
        self.selectionChanged.emit()

    @Slot(str, str, int)
    def updateTransition(self, transition_id: str, kind: str, duration: int) -> None:
        try:
            self._require_writable()
            self._editor.update_transition(
                transition_id,
                kind=TransitionKind(kind),
                duration=max(1, duration),
            )
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeTransition(self, transition_id: str) -> None:
        try:
            self._require_writable()
            self._editor.remove_transition(transition_id)
            self._selected_transition_id = ""
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int)
    def addTimelineMarker(self, frame: int) -> None:
        try:
            self._require_writable()
            marker = self._editor.add_marker(max(0, frame), f"标记 {len(self._editor.state.markers) + 1}")
            self._selected_marker_id = marker.id
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeTimelineMarker(self, marker_id: str) -> None:
        try:
            self._require_writable()
            self._editor.remove_marker(marker_id)
            self._selected_marker_id = ""
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int)
    def setRangeIn(self, frame: int) -> None:
        self._range_in_frame = max(0, frame)
        self.selectionChanged.emit()

    @Slot(int)
    def setSequenceInPoint(self, frame: int) -> None:
        try:
            self._require_writable()
            out_frame = self.sequenceOutFrame
            self._editor.set_sequence_in_out(max(0, frame), out_frame)
            self._finish_sequence_in_out_edit("已设置序列入点")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int)
    def setSequenceOutPoint(self, frame: int) -> None:
        try:
            self._require_writable()
            in_frame = self.sequenceInFrame
            self._editor.set_sequence_in_out(in_frame, max(1, frame))
            self._finish_sequence_in_out_edit("已设置序列出点")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, int)
    def setSequenceInOut(self, in_frame: int, out_frame: int) -> None:
        try:
            self._require_writable()
            self._editor.set_sequence_in_out(in_frame, out_frame)
            self._finish_sequence_in_out_edit("已调整序列入出点")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def clearSequenceInOut(self) -> None:
        try:
            self._require_writable()
            self._editor.clear_sequence_in_out()
            self._finish_sequence_in_out_edit("已清除序列入出点")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int)
    def commitTimelineRange(self, frame: int) -> None:
        try:
            self._require_writable()
            if self._range_in_frame is None:
                raise ValueError("请先设置选区入点")
            start_frame, end_frame = sorted((self._range_in_frame, max(0, frame)))
            if start_frame == end_frame:
                raise ValueError("选区必须包含至少一帧")
            item = self._editor.add_range(
                start_frame,
                end_frame,
                f"选区 {len(self._editor.state.ranges) + 1}",
            )
            self._selected_range_id = item.id
            self._range_in_frame = None
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeTimelineRange(self, range_id: str) -> None:
        try:
            self._require_writable()
            self._editor.remove_range(range_id)
            self._selected_range_id = ""
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def createShortFromRange(self, range_id: str) -> None:
        try:
            self._require_writable()
            sequence = self._project.sequences.create_short_from_range(
                self._active_sequence_id,
                range_id,
            )
            self._active_sequence_id = sequence.id
            self._editor = self._project.timeline(sequence.id)
            self._selected_range_id = ""
            self._projector.refresh_all()
            self._set_status("已从时间线选区创建短视频序列")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int, int)
    def trimClip(self, clip_id: str, source_in: int, duration: int) -> None:
        try:
            self._require_writable()
            clip = next(item for item in self._editor.state.clips if item.id == clip_id)
            self._editor.trim_clip(
                clip_id,
                timeline_start=clip.timeline_start,
                source_in=max(0, source_in),
                duration=max(1, duration),
            )
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int, int, bool)
    def trimClipEdges(
        self,
        clip_id: str,
        timeline_start: int,
        duration: int,
        trim_left: bool,
    ) -> None:
        try:
            self._require_writable()
            clip = next(item for item in self._editor.state.clips if item.id == clip_id)
            source_in = clip.source_in
            if trim_left:
                delta = timeline_start - clip.timeline_start
                source_delta = source_frames_for_timeline_frames(
                    delta,
                    clip.speed_numerator,
                    clip.speed_denominator,
                )
                source_in = (
                    clip.source_in + source_delta
                    if clip.speed_numerator > 0
                    else clip.source_in - source_delta
                )
            self._editor.trim_clip(
                clip_id,
                timeline_start=max(0, timeline_start),
                source_in=max(0, source_in),
                duration=max(1, duration),
            )
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, bool)
    def setClipSpeed(self, clip_id: str, speed: float, pitch_compensation: bool) -> None:
        try:
            self._require_writable()
            if abs(speed) < 0.25 or abs(speed) > 4.0:
                raise ValueError("速度必须在 0.25×～4× 或 -0.25×～-4×之间")
            fraction = Fraction(str(speed)).limit_denominator(1000)
            self._editor.set_clip_speed(
                clip_id,
                speed_numerator=fraction.numerator,
                speed_denominator=fraction.denominator,
                pitch_compensation=pitch_compensation,
            )
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, float, float, float, float, float, float, float, float, float)
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
        try:
            self._require_writable()
            self._editor.set_clip_transform(
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
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, float, float, int, int)
    def setClipAudio(
        self,
        clip_id: str,
        gain_db: float,
        pan: float,
        fade_in_frames: int,
        fade_out_frames: int,
    ) -> None:
        try:
            self._require_writable()
            self._editor.set_clip_audio(
                clip_id,
                ClipAudio(
                    gain_db=max(-60.0, min(24.0, gain_db)),
                    pan=pan,
                    fade_in_frames=max(0, fade_in_frames),
                    fade_out_frames=max(0, fade_out_frames),
                ),
            )
            self._projector.refresh_timeline()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def undo(self) -> None:
        try:
            self._editor.undo()
            self._projector.refresh_assets()
            self._projector.refresh_sequences()
            self._projector.refresh_timeline()
            self._projector.refresh_documents()
            self._projector.refresh_preview_subtitles()
            self._projector.schedule_preview_graph()
            self.projectStateChanged.emit()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def redo(self) -> None:
        try:
            self._editor.redo()
            self._projector.refresh_assets()
            self._projector.refresh_sequences()
            self._projector.refresh_timeline()
            self._projector.refresh_documents()
            self._projector.refresh_preview_subtitles()
            self._projector.schedule_preview_graph()
            self.projectStateChanged.emit()
            self.selectionChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def analyzeSequenceBoundaries(self) -> None:
        try:
            self._require_writable()
            if not self._editor.state.clips:
                raise ValueError("请先向时间线添加媒体")
            if self.sequenceBoundaryAnalysisRunning:
                raise RuntimeError("当前序列正在分析入出点")
            snapshot_hash = self._project.sequence_boundary_snapshot_hash(self._active_sequence_id)
            self._start_task(
                AnalyzeSequenceBoundsCommand(
                    sequence_id=self._active_sequence_id,
                    snapshot_hash=snapshot_hash,
                ),
                sequence_id=self._active_sequence_id,
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))
