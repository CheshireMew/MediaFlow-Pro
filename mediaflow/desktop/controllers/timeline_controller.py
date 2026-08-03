from __future__ import annotations

from fractions import Fraction

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.desktop.presentation_catalogs import (
    transition_options,
)
from mediaflow.desktop.session_state import TimelinePlacement
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import (
    ColorMode,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.task_commands import (
    AnalyzeScenesCommand,
    AnalyzeSequenceBoundsCommand,
    TrackSubjectCommand,
)
from mediaflow.domain.timebase import (
    source_frames_for_timeline_frames,
)
from mediaflow.domain.timeline import ClipAudio, ClipTransform, Transition
from mediaflow.domain.visual_effects import VISUAL_EFFECT_SPECS

from .controller_facet import ControllerFacet, report_ui_errors


class TimelineController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    previewRangeRequested = Signal(int, int)
    exclusiveSelectionRequested = Signal()
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def tracksModel(self) -> QObject:
        return self._session.models.tracks

    @Property(QObject, constant=True)
    def clipsModel(self) -> QObject:
        return self._session.models.clips

    @Property(QObject, constant=True)
    def compoundClipsModel(self) -> QObject:
        return self._session.models.compound_clips

    @Property(QObject, constant=True)
    def transitionsModel(self) -> QObject:
        return self._session.models.transitions

    @Property(QObject, constant=True)
    def timelineMarkersModel(self) -> QObject:
        return self._session.models.markers

    @Property(QObject, constant=True)
    def timelineRangesModel(self) -> QObject:
        return self._session.models.ranges

    @Property("QVariantList", notify=projectStateChanged)
    def transitionOptions(self) -> list[dict]:
        color_mode = (
            self._session.binding.timeline.state.sequence.profile.color_mode
            if self._session.binding.timeline
            else ColorMode.SDR_BT709
        )
        return transition_options(color_mode)

    @Slot(str, str, int)
    def previewTransitionAfter(self, clip_id: str, kind: str, duration: int) -> None:
        """Compile a temporary transition through the real MLT preview graph."""
        try:
            if not self._session.binding.current or not self._session.binding.timeline or not clip_id:
                return
            state = self._session.binding.timeline.state
            left = next(item for item in state.clips if item.id == clip_id)
            right = next(
                item
                for item in state.clips_for_track(left.track_id)
                if item.timeline_start == left.timeline_end
            )
            transition_kind = TransitionKind(kind)
            if not transition_is_available(transition_kind, state.sequence.profile.color_mode):
                return
            transition_duration = max(1, min(int(duration), left.duration, right.duration))
            preview = Transition(
                track_id=left.track_id,
                left_clip_id=left.id,
                right_clip_id=right.id,
                kind=transition_kind,
                duration=transition_duration,
            )
            state.transitions = [
                item
                for item in state.transitions
                if item.left_clip_id != left.id and item.right_clip_id != right.id
            ]
            state.transitions.append(preview)
            path = self._session._api.write_preview_snapshot(
                self._session.binding.current.project_dir,
                state,
                use_proxies=self._session.settings.preview.preview_quality != "source",
                prefer_sdr_preview_proxy=(
                    state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ
                    and not self._session.presentation.hdr_preview_active
                ),
            )
            self._session.presentation.preview_graph_path = str(path)
            self._session.events.previewGraphChanged.emit()
            self._session.events.previewRangeRequested.emit(
                max(0, left.timeline_end - transition_duration),
                min(state.duration_frames, left.timeline_end + transition_duration),
            )
        except (KeyError, StopIteration, ValueError):
            return
        except Exception as error:
            self._session.events.errorOccurred.emit(str(error))

    @Slot()
    def clearTransitionPreview(self) -> None:
        if self._session.binding.timeline and self._session.binding.timeline.state.clips:
            self._session.projectors.timeline.schedule_preview_graph()

    @Property(bool, notify=tasksChanged)
    def sequenceBoundaryAnalysisRunning(self) -> bool:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return False
        return any(
            isinstance(task.command, AnalyzeSequenceBoundsCommand)
            and task.command.sequence_id == self._session.binding.active_sequence_id
            and task.status.is_active
            for task in self._session.task_state.items.values()
        )

    @Property(str, notify=selectionChanged)
    def selectedClipId(self) -> str:
        if self._session.selection.compound_id:
            return ""
        return self._session.selection.clip_ids[-1] if self._session.selection.clip_ids else ""

    @Property("QVariantList", notify=selectionChanged)
    def selectedClipIds(self) -> list[str]:
        return list(self._session.selection.clip_ids)

    @Property(str, notify=selectionChanged)
    def selectedCompoundId(self) -> str:
        return self._session.selection.compound_id

    @Property(str, notify=selectionChanged)
    def selectedTransitionId(self) -> str:
        return self._session.selection.transition_id

    @Property(str, notify=selectionChanged)
    def selectedMarkerId(self) -> str:
        return self._session.selection.marker_id

    @Property(str, notify=selectionChanged)
    def selectedRangeId(self) -> str:
        return self._session.selection.range_id

    @Property(int, notify=selectionChanged)
    def rangeInFrame(self) -> int:
        return (
            -1 if self._session.selection.range_in_frame is None else self._session.selection.range_in_frame
        )

    @Property("QVariantMap", notify=selectionChanged)
    def selectedTransitionData(self) -> dict:
        row = self._session.models.transitions.findRow("transitionId", self._session.selection.transition_id)
        return self._session.models.transitions.get(row)

    @Property("QVariantMap", notify=selectionChanged)
    def selectedClipData(self) -> dict:
        row = self._session.models.clips.findRow("clipId", self.selectedClipId)
        return self._session.models.clips.get(row)

    @Property("QVariantList", notify=selectionChanged)
    def selectedClipReplacementOptions(self) -> list[dict]:
        if not self._session.binding.current or not self.selectedClipId:
            return []
        row = self.selectedClipData
        track_kind = str(row.get("trackKind", ""))
        options = []
        for asset in self._session.binding.current.list_assets():
            if asset.kind.value == "web" or asset.status.value != "online":
                continue
            compatible = (
                track_kind == TrackKind.VIDEO.value
                and asset.kind.value in {"video", "image"}
            ) or (
                track_kind == TrackKind.AUDIO.value
                and asset.kind.value in {"video", "audio"}
                and asset.metadata.has_audio
            )
            if compatible:
                options.append({"label": asset.name, "value": asset.id})
        return options

    @Property("QVariantList", notify=selectionChanged)
    def visualEffectOptions(self) -> list[dict]:
        return [
            {"label": str(spec["label"]), "value": kind.value}
            for kind, spec in VISUAL_EFFECT_SPECS.items()
        ]

    @Property("QVariantList", notify=selectionChanged)
    def selectedClipVisualEffects(self) -> list[dict]:
        if not self._session.binding.timeline or not self.selectedClipId:
            return []
        clip = next(
            item
            for item in self._session.binding.timeline.state.clips
            if item.id == self.selectedClipId
        )
        rows = []
        for effect in clip.visual_effects:
            schema = VISUAL_EFFECT_SPECS[effect.kind]
            rows.append(
                {
                    "effectId": effect.id,
                    "kind": effect.kind.value,
                    "label": str(schema["label"]),
                    "position": effect.position,
                    "enabled": effect.enabled,
                    "parameters": dict(effect.parameters),
                    "parameterSpecs": [
                        {"key": key, **values}
                        for key, values in schema["parameters"].items()
                    ],
                }
            )
        return rows

    @Property("QVariantMap", notify=selectionChanged)
    def selectedClipsSummary(self) -> dict:
        rows = [
            self._session.models.clips.get(
                self._session.models.clips.findRow("clipId", clip_id)
            )
            for clip_id in self._session.selection.clip_ids
        ]
        rows = [row for row in rows if row]
        if len(rows) < 2:
            return {}

        def common(key: str):
            values = [row.get(key) for row in rows]
            return values[0] if all(value == values[0] for value in values) else None

        return {
            "count": len(rows),
            "totalDurationFrames": sum(int(row["durationFrames"]) for row in rows),
            "assetKinds": sorted({str(row["assetKind"]) for row in rows}),
            "gainDb": common("gainDb"),
            "pan": common("pan"),
            "fadeInFrames": common("fadeInFrames"),
            "fadeOutFrames": common("fadeOutFrames"),
            "opacity": common("opacity"),
        }

    @Property("QVariantMap", notify=selectionChanged)
    def selectedCompoundData(self) -> dict:
        row = self._session.models.compound_clips.findRow("compoundId", self._session.selection.compound_id)
        return self._session.models.compound_clips.get(row)

    @Property(bool, notify=selectionChanged)
    def canCreateCompoundClip(self) -> bool:
        if (
            not self._session.binding.timeline
            or self._session.selection.compound_id
            or len(self._session.selection.clip_ids) < 2
        ):
            return False
        selected_ids = set(self._session.selection.clip_ids)
        if any(
            selected_ids.intersection(item.clip_ids)
            for item in self._session.binding.timeline.state.compounds
        ):
            return False
        selected = sorted(
            (clip for clip in self._session.binding.timeline.state.clips if clip.id in selected_ids),
            key=lambda clip: (clip.timeline_start, clip.id),
        )
        return (
            len(selected) == len(selected_ids)
            and len({clip.track_id for clip in selected}) == 1
            and all(
                left.timeline_end == right.timeline_start
                for left, right in zip(selected, selected[1:], strict=False)
            )
        )

    @Property(bool, notify=historyChanged)
    def canUndo(self) -> bool:
        return bool(self._session.binding.timeline and self._session.binding.timeline.can_undo)

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:
        return bool(self._session.binding.timeline and self._session.binding.timeline.can_redo)

    @Slot(str)
    @Slot(str, bool)
    def selectClip(self, clip_id: str, toggle: bool = False) -> None:
        self._session.selection.compound_id = ""
        self._session.selection.clip_ids = self._session._updated_selection(
            self._session.selection.clip_ids,
            clip_id,
            toggle=toggle,
        )
        self._session.selection.transition_id = ""
        self._session.events.selectionChanged.emit()

    @Slot(str)
    def selectCompoundClip(self, compound_id: str) -> None:
        if not self._session.binding.timeline:
            return
        try:
            compound = next(
                item for item in self._session.binding.timeline.state.compounds if item.id == compound_id
            )
        except StopIteration:
            return
        self._session.selection.compound_id = compound.id
        self._session.selection.clip_ids = list(compound.clip_ids)
        self._session.selection.transition_id = ""
        self._session.selection.marker_id = ""
        self._session.selection.range_id = ""
        self._session.events.selectionChanged.emit()

    @Slot(str, result=bool)
    def isClipSelected(self, clip_id: str) -> bool:
        return clip_id in self._session.selection.clip_ids

    @Slot()
    def selectAllClips(self) -> None:
        self._session.selection.clip_ids = (
            [clip.id for clip in self._session.binding.timeline.state.clips]
            if self._session.binding.timeline
            else []
        )
        self._session.selection.compound_id = ""
        self._session.selection.transition_id = ""
        self._session.selection.marker_id = ""
        self._session.selection.range_id = ""
        self._session.events.selectionChanged.emit()

    @Slot()
    def clearSelection(self) -> None:
        self._session.selection.clip_ids = []
        self._session.selection.compound_id = ""
        self._session.selection.transition_id = ""
        self._session.selection.marker_id = ""
        self._session.selection.range_id = ""
        self._session.events.selectionChanged.emit()

    @Slot(str)
    @report_ui_errors
    def addTrack(self, kind: str) -> None:
        self._session._require_writable()
        track_kind = TrackKind(kind)
        self._session.timeline_assets.add_timeline_track(track_kind)
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

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

    @Slot(str, bool, bool, bool, bool, str)
    @report_ui_errors
    def updateTrack(
        self,
        track_id: str,
        enabled: bool,
        locked: bool,
        muted: bool,
        solo: bool,
        audio_bus_id: str,
    ) -> None:
        self._session._require_writable()
        self._session.binding.timeline.set_track_state(
            track_id,
            enabled=enabled,
            locked=locked,
            muted=muted,
            solo=solo,
            audio_bus_id=audio_bus_id or None,
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.historyChanged.emit()

    @Slot(str)
    @report_ui_errors
    def setPrimaryDialogueTrack(self, track_id: str) -> None:
        self._session._require_writable()
        self._session.binding.timeline.set_primary_dialogue_track(track_id)
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.historyChanged.emit()

    @Slot(str, int)
    @report_ui_errors
    def moveTrack(self, track_id: str, position: int) -> None:
        self._session._require_writable()
        self._session.binding.timeline.move_track(track_id, position)
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.historyChanged.emit()

    @Slot(str, int, int, bool, result="QVariantMap")
    def previewClipMove(
        self,
        clip_id: str,
        start_frame: int,
        requested_track_position: int,
        from_linked_audio: bool,
    ) -> dict:
        tracks = sorted(self._session.binding.timeline.state.tracks, key=lambda item: item.position)
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
            self._session.selection.clip_ids if clip_id in self._session.selection.clip_ids else [clip_id]
        )
        try:
            self._session.binding.timeline.preview_move_clips(
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
            self._session.selection.clip_ids if clip_id in self._session.selection.clip_ids else [clip_id]
        )
        targets = self._session._timeline_snap_targets(selected_ids, playhead_frame) if snap_enabled else []
        tolerance = self._session._snap_tolerance_frames(pixels_per_frame) if snap_enabled else 0
        if len(selected_ids) > 1:
            source = next(item for item in self._session.binding.timeline.state.clips if item.id == clip_id)
            self._session.binding.timeline.move_clips(
                selected_ids,
                primary_clip_id=clip_id,
                timeline_start=max(0, start_frame),
                track_id=track_id or source.track_id,
                snap_targets=targets,
                snap_tolerance_frames=tolerance,
            )
        else:
            self._session.binding.timeline.move_clip(
                clip_id,
                timeline_start=max(0, start_frame),
                track_id=track_id or None,
                snap_targets=targets,
                snap_tolerance_frames=tolerance,
            )
        self._session.projectors.timeline.refresh_timeline(defer_clip_updates=True)
        self._session.events.historyChanged.emit()

    @Slot(str, float, int)
    @report_ui_errors
    def duplicateClip(self, clip_id: str, pixels_per_frame: float, playhead_frame: int) -> None:
        self._session._require_writable()
        source = next(item for item in self._session.binding.timeline.state.clips if item.id == clip_id)
        copied = self._session.binding.timeline.copy_clip(
            clip_id,
            timeline_start=source.timeline_end,
            snap_targets=self._session._timeline_snap_targets([clip_id], playhead_frame),
            snap_tolerance_frames=self._session._snap_tolerance_frames(pixels_per_frame),
        )
        self._session.selection.clip_ids = [copied.id]
        self._session.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(str, int)
    @report_ui_errors
    def splitClip(self, clip_id: str, frame: int) -> None:
        self._session._require_writable()
        _, right = self._session.binding.timeline.split_clip(clip_id, frame)
        self._session.selection.clip_ids = [right.id]
        self._session.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(str)
    @report_ui_errors
    def detachClipAudio(self, clip_id: str) -> None:
        self._session._require_writable()
        video, _audio = self._session.binding.timeline.detach_clip_audio(clip_id)
        self._session.selection.clip_ids = [video.id]
        self._session.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.schedule_preview_graph()
        self.exclusiveSelectionRequested.emit()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()
        self._session._set_status("已解除视音频绑定；当前仅选中视频。点击空白处或按 Esc 可清除选择")

    @Slot(bool)
    @report_ui_errors
    def deleteSelectedClips(self, ripple: bool = False) -> None:
        self._session._require_writable()
        if not self._session.selection.clip_ids:
            return
        self._session.binding.timeline.delete_clips(self._session.selection.clip_ids, ripple=ripple)
        self._session.selection.clip_ids = []
        self._session.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot()
    @report_ui_errors
    def createCompoundClip(self) -> None:
        self._session._require_writable()
        compound = self._session.binding.timeline.create_compound_clip(self._session.selection.clip_ids)
        self._session.selection.compound_id = compound.id
        self._session.selection.clip_ids = list(compound.clip_ids)
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()
        self._session._set_status("已创建复合片段")

    @Slot()
    @report_ui_errors
    def dissolveSelectedCompoundClip(self) -> None:
        self._session._require_writable()
        if not self._session.selection.compound_id:
            return
        selected_ids = list(self._session.selection.clip_ids)
        self._session.binding.timeline.dissolve_compound_clip(self._session.selection.compound_id)
        self._session.selection.compound_id = ""
        self._session.selection.clip_ids = selected_ids
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()
        self._session._set_status("已解除复合片段")

    @Slot(str, str, int)
    def addTransitionAfter(self, clip_id: str, kind: str, duration: int) -> None:
        try:
            self._session._require_writable()
            state = self._session.binding.timeline.state
            left = next(clip for clip in state.clips if clip.id == clip_id)
            right = next(
                clip
                for clip in state.clips_for_track(left.track_id)
                if clip.timeline_start == left.timeline_end
            )
            transition = self._session.binding.timeline.create_transition(
                left.id,
                right.id,
                TransitionKind(kind),
                max(1, duration),
            )
            self._session.selection.transition_id = transition.id
            self._session.projectors.timeline.refresh_timeline()
            self._session.events.selectionChanged.emit()
            self._session.events.historyChanged.emit()
            self._session._set_status("转场已添加")
        except StopIteration:
            self._session.events.errorOccurred.emit("所选片段后没有同轨道的相邻片段")
        except Exception as error:
            self._session.events.errorOccurred.emit(str(error))

    @Slot(str)
    def selectTransition(self, transition_id: str) -> None:
        self._session.selection.transition_id = transition_id
        self._session.selection.clip_ids = []
        self._session.selection.compound_id = ""
        self._session.events.selectionChanged.emit()

    @Slot(str)
    def selectTimelineRange(self, range_id: str) -> None:
        self._session.selection.range_id = range_id
        self._session.events.selectionChanged.emit()

    @Slot(str, str, int)
    @report_ui_errors
    def updateTransition(self, transition_id: str, kind: str, duration: int) -> None:
        self._session._require_writable()
        self._session.binding.timeline.update_transition(
            transition_id,
            kind=TransitionKind(kind),
            duration=max(1, duration),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(str)
    @report_ui_errors
    def removeTransition(self, transition_id: str) -> None:
        self._session._require_writable()
        self._session.binding.timeline.remove_transition(transition_id)
        self._session.selection.transition_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(int)
    @report_ui_errors
    def addTimelineMarker(self, frame: int) -> None:
        self._session._require_writable()
        marker = self._session.binding.timeline.add_marker(
            max(0, frame), f"标记 {len(self._session.binding.timeline.state.markers) + 1}"
        )
        self._session.selection.marker_id = marker.id
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(str)
    @report_ui_errors
    def removeTimelineMarker(self, marker_id: str) -> None:
        self._session._require_writable()
        self._session.binding.timeline.remove_marker(marker_id)
        self._session.selection.marker_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(int)
    def setRangeIn(self, frame: int) -> None:
        self._session.selection.range_in_frame = max(0, frame)
        self._session.events.selectionChanged.emit()

    @Slot(int)
    @report_ui_errors
    def setSequenceInPoint(self, frame: int) -> None:
        self._session._require_writable()
        in_out = self._session.binding.timeline.state.sequence.in_out
        out_frame = in_out.out_frame if in_out else max(1, frame + 1)
        self._session.binding.timeline.set_sequence_in_out(max(0, frame), out_frame)
        self._session._finish_sequence_in_out_edit("已设置序列入点")

    @Slot(int)
    @report_ui_errors
    def setSequenceOutPoint(self, frame: int) -> None:
        self._session._require_writable()
        in_out = self._session.binding.timeline.state.sequence.in_out
        in_frame = in_out.in_frame if in_out else 0
        self._session.binding.timeline.set_sequence_in_out(in_frame, max(1, frame))
        self._session._finish_sequence_in_out_edit("已设置序列出点")

    @Slot(int, int)
    @report_ui_errors
    def setSequenceInOut(self, in_frame: int, out_frame: int) -> None:
        self._session._require_writable()
        self._session.binding.timeline.set_sequence_in_out(in_frame, out_frame)
        self._session._finish_sequence_in_out_edit("已调整序列入出点")

    @Slot()
    @report_ui_errors
    def clearSequenceInOut(self) -> None:
        self._session._require_writable()
        self._session.binding.timeline.clear_sequence_in_out()
        self._session._finish_sequence_in_out_edit("已清除序列入出点")

    @Slot(int)
    @report_ui_errors
    def commitTimelineRange(self, frame: int) -> None:
        self._session._require_writable()
        if self._session.selection.range_in_frame is None:
            raise ValueError("请先设置选区入点")
        start_frame, end_frame = sorted((self._session.selection.range_in_frame, max(0, frame)))
        if start_frame == end_frame:
            raise ValueError("选区必须包含至少一帧")
        item = self._session.binding.timeline.add_range(
            start_frame,
            end_frame,
            f"选区 {len(self._session.binding.timeline.state.ranges) + 1}",
        )
        self._session.selection.range_id = item.id
        self._session.selection.range_in_frame = None
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(str)
    @report_ui_errors
    def removeTimelineRange(self, range_id: str) -> None:
        self._session._require_writable()
        self._session.binding.timeline.remove_range(range_id)
        self._session.selection.range_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(str)
    @report_ui_errors
    def createShortFromRange(self, range_id: str) -> None:
        self._session._require_writable()
        sequence = self._session.binding.current.create_short_from_range(
            self._session.binding.active_sequence_id,
            range_id,
        )
        self._session.binding.active_sequence_id = sequence.id
        self._session.binding.timeline = self._session.binding.current.timeline(sequence.id)
        self._session.selection.range_id = ""
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("已从时间线选区创建短视频序列")

    @Slot(str, int, int)
    @report_ui_errors
    def trimClip(self, clip_id: str, source_in: int, duration: int) -> None:
        self._session._require_writable()
        clip = next(item for item in self._session.binding.timeline.state.clips if item.id == clip_id)
        self._session.binding.timeline.trim_clip(
            clip_id,
            timeline_start=clip.timeline_start,
            source_in=max(0, source_in),
            duration=max(1, duration),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

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
        clip = next(item for item in self._session.binding.timeline.state.clips if item.id == clip_id)
        source_in = clip.source_in
        if trim_left:
            delta = timeline_start - clip.timeline_start
            source_delta = source_frames_for_timeline_frames(
                delta,
                clip.speed_numerator,
                clip.speed_denominator,
            )
            source_in = (
                clip.source_in + source_delta if clip.speed_numerator > 0 else clip.source_in - source_delta
            )
        self._session.binding.timeline.trim_clip(
            clip_id,
            timeline_start=max(0, timeline_start),
            source_in=max(0, source_in),
            duration=max(1, duration),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(str, float, bool)
    @report_ui_errors
    def setClipSpeed(self, clip_id: str, speed: float, pitch_compensation: bool) -> None:
        self._session._require_writable()
        if abs(speed) < 0.25 or abs(speed) > 4.0:
            raise ValueError("速度必须在 0.25×～4× 或 -0.25×～-4×之间")
        fraction = Fraction(str(speed)).limit_denominator(1000)
        self._session.binding.timeline.set_clip_speed(
            clip_id,
            speed_numerator=fraction.numerator,
            speed_denominator=fraction.denominator,
            pitch_compensation=pitch_compensation,
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

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
        self._session.binding.timeline.set_clip_transform(
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
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    def _selected_video_clip(self):
        if not self._session.binding.timeline or len(self._session.selection.clip_ids) != 1:
            raise ValueError("请先选择一个视频片段")
        clip = next(
            item
            for item in self._session.binding.timeline.state.clips
            if item.id == self._session.selection.clip_ids[0]
        )
        asset = self._session.binding.current.get_asset(clip.asset_id)
        if asset.kind.value != "video":
            raise ValueError("此操作只适用于视频片段")
        return clip

    @Slot(float)
    @report_ui_errors
    def detectScenesSelected(self, threshold: float = 0.35) -> None:
        self._session._require_writable()
        clip = self._selected_video_clip()
        self._session.tasks.start(
            AnalyzeScenesCommand(
                sequence_id=self._session.binding.active_sequence_id,
                clip_id=clip.id,
                threshold=threshold,
            ),
            [clip.asset_id],
            sequence_id=self._session.binding.active_sequence_id,
        )
        self._session._set_status("正在检测场景切点")

    @Slot()
    def autoReframeSelected(self) -> None:
        self._start_subject_tracking("auto_reframe")

    @Slot()
    def trackSubjectSelected(self) -> None:
        self._start_subject_tracking("subject_tracking")

    @report_ui_errors
    def _start_subject_tracking(self, mode: str) -> None:
        self._session._require_writable()
        clip = self._selected_video_clip()
        self._session.tasks.start(
            TrackSubjectCommand(
                sequence_id=self._session.binding.active_sequence_id,
                clip_id=clip.id,
                mode=mode,
            ),
            [clip.asset_id],
            sequence_id=self._session.binding.active_sequence_id,
        )
        self._session._set_status("正在分析画面主体")

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
        self._session.binding.timeline.set_clip_audio(
            clip_id,
            ClipAudio(
                gain_db=max(-60.0, min(24.0, gain_db)),
                pan=pan,
                fade_in_frames=max(0, fade_in_frames),
                fade_out_frames=max(0, fade_out_frames),
            ),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot(str)
    @report_ui_errors
    def replaceSelectedClipSource(self, asset_id: str) -> None:
        self._session._require_writable()
        if not self.selectedClipId:
            raise ValueError("请先选择一个片段")
        self._session.binding.timeline.replace_clip_source(
            self.selectedClipId,
            asset_id,
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()
        self._session._set_status("片段素材已替换")

    @Slot(str)
    @report_ui_errors
    def addSelectedClipVisualEffect(self, kind: str) -> None:
        self._session._require_writable()
        if not self.selectedClipId:
            raise ValueError("请先选择一个片段")
        self._session.binding.timeline.add_clip_visual_effect(
            self.selectedClipId,
            VisualEffectKind(kind),
        )
        self._after_visual_effect_change("视觉效果已添加")

    @Slot(str, bool)
    @report_ui_errors
    def setSelectedClipVisualEffectEnabled(self, effect_id: str, enabled: bool) -> None:
        self._session._require_writable()
        clip, effect = self._selected_visual_effect(effect_id)
        self._session.binding.timeline.update_clip_visual_effect(
            clip.id,
            effect.id,
            enabled=enabled,
            parameters=effect.parameters,
        )
        self._after_visual_effect_change("视觉效果已更新")

    @Slot(str, str, float)
    @report_ui_errors
    def setSelectedClipVisualEffectParameter(
        self,
        effect_id: str,
        key: str,
        value: float,
    ) -> None:
        self._session._require_writable()
        clip, effect = self._selected_visual_effect(effect_id)
        parameters = dict(effect.parameters)
        if key not in parameters:
            raise ValueError("未知的视觉效果参数")
        parameters[key] = value
        self._session.binding.timeline.update_clip_visual_effect(
            clip.id,
            effect.id,
            enabled=effect.enabled,
            parameters=parameters,
        )
        self._after_visual_effect_change("视觉效果已更新")

    @Slot(str, int)
    @report_ui_errors
    def moveSelectedClipVisualEffect(self, effect_id: str, position: int) -> None:
        self._session._require_writable()
        clip, _effect = self._selected_visual_effect(effect_id)
        self._session.binding.timeline.move_clip_visual_effect(
            clip.id,
            effect_id,
            position,
        )
        self._after_visual_effect_change("视觉效果顺序已更新")

    @Slot(str)
    @report_ui_errors
    def removeSelectedClipVisualEffect(self, effect_id: str) -> None:
        self._session._require_writable()
        clip, _effect = self._selected_visual_effect(effect_id)
        self._session.binding.timeline.remove_clip_visual_effect(clip.id, effect_id)
        self._after_visual_effect_change("视觉效果已移除")

    def _selected_visual_effect(self, effect_id: str):
        if not self._session.binding.timeline or not self.selectedClipId:
            raise ValueError("请先选择一个片段")
        clip = next(
            item
            for item in self._session.binding.timeline.state.clips
            if item.id == self.selectedClipId
        )
        effect = next(item for item in clip.visual_effects if item.id == effect_id)
        return clip, effect

    def _after_visual_effect_change(self, status: str) -> None:
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()
        self._session._set_status(status)

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
        self._session.binding.timeline.set_clips_properties(
            self._session.selection.clip_ids,
            gain_db=max(-60.0, min(24.0, gain_db)),
            pan=max(-1.0, min(1.0, pan)),
            fade_in_frames=max(0, fade_in_frames),
            fade_out_frames=max(0, fade_out_frames),
            opacity=max(0.0, min(1.0, opacity)),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot()
    @report_ui_errors
    def undo(self) -> None:
        self._session.binding.timeline.undo()
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.refresh_sequences()
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.projectStateChanged.emit()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot()
    @report_ui_errors
    def redo(self) -> None:
        self._session.binding.timeline.redo()
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.refresh_sequences()
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.projectStateChanged.emit()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()

    @Slot()
    @report_ui_errors
    def analyzeSequenceBoundaries(self) -> None:
        self._session._require_writable()
        if not self._session.binding.timeline.state.clips:
            raise ValueError("请先向时间线添加媒体")
        if self.sequenceBoundaryAnalysisRunning:
            raise RuntimeError("当前序列正在分析入出点")
        snapshot_hash = self._session.binding.current.sequence_boundary_snapshot_hash(
            self._session.binding.active_sequence_id
        )
        self._session.tasks.start(
            AnalyzeSequenceBoundsCommand(
                sequence_id=self._session.binding.active_sequence_id,
                snapshot_hash=snapshot_hash,
            ),
            sequence_id=self._session.binding.active_sequence_id,
        )
