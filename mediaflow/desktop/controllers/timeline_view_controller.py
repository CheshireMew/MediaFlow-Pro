from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.desktop.presentation_timeline import transition_options
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import ColorMode, TrackKind, TransitionKind
from mediaflow.domain.timeline import Transition

from .controller_facet import ControllerFacet
from .controller_scopes import TimelinePresentationScope
from .timeline_selection import sequence_boundary_analysis_running


class TimelineViewController(ControllerFacet[TimelinePresentationScope]):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    previewRangeRequested = Signal(int, int)

    @Property(QObject, constant=True)
    def tracksModel(self) -> QObject:
        return self._session.models.tracks

    @Property(QObject, constant=True)
    def clipsModel(self) -> QObject:
        return self._session.models.clips

    @Slot(float, float, float, int)
    def requestFilmstrip(
        self,
        visible_start_frame: float,
        visible_end_frame: float,
        pixels_per_frame: float,
        height: int,
    ) -> None:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return
        self._session.state.requests.filmstrip_id += 1
        filmstrip_generation = self._session.state.requests.filmstrip_id
        request_id = (
            self._session.state.binding.generation,
            filmstrip_generation,
            self._session.state.binding.active_sequence_id,
        )
        previous = self._session.state.requests.filmstrip_future
        if previous is not None and not previous.done():
            previous.cancel()
        project_dir = self._session.state.binding.require_current().project_dir
        sequence_id = self._session.state.binding.active_sequence_id
        request_owner = self._session.state.binding.require_current().actor_id
        self._session.state.requests.filmstrip_future = self._session.background.submit(
            "timeline_filmstrip",
            request_id,
            lambda: self._session._api.timeline_filmstrip_paths(
                project_dir,
                sequence_id,
                visible_start_frame=max(0, int(visible_start_frame)),
                visible_end_frame=max(1, int(visible_end_frame + 0.999999)),
                pixels_per_frame=float(pixels_per_frame),
                height=max(1, int(height)),
                request_owner=request_owner,
                request_generation=filmstrip_generation,
            ),
        )

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

    @Property(list, notify=projectStateChanged)
    def transitionOptions(self) -> list[dict]:
        color_mode = (
            self._session.state.binding.require_timeline().state.sequence.profile.color_mode
            if self._session.state.binding.timeline
            else ColorMode.SDR_BT709
        )
        return transition_options(color_mode)

    @Slot(str, str, int)
    def previewTransitionAfter(self, clip_id: str, kind: str, duration: int) -> None:
        """Compile a temporary transition through the real MLT preview graph."""
        try:
            if (
                not self._session.state.binding.current
                or not self._session.state.binding.timeline
                or not clip_id
            ):
                return
            state = self._session.state.binding.require_timeline().state
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
                self._session.state.binding.require_current().project_dir,
                state,
                use_proxies=self._session.state.service_settings.preview.preview_quality != "source",
                prefer_sdr_preview_proxy=(
                    state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ
                    and not self._session.state.presentation.hdr_preview_active
                ),
            )
            self._session.state.presentation.preview_graph_path = str(path)
            self._session.updates.commit(preview_graph=True)
            self._session.updates.request_preview_range(
                max(0, left.timeline_end - transition_duration),
                min(state.duration_frames, left.timeline_end + transition_duration),
            )
        except (KeyError, StopIteration, ValueError):
            return
        except Exception as error:
            self._session.updates.report_error(str(error))

    @Slot()
    def clearTransitionPreview(self) -> None:
        if (
            self._session.state.binding.timeline
            and self._session.state.binding.require_timeline().state.clips
        ):
            self._session.projectors.timeline.schedule_preview_graph()

    @Property(bool, notify=tasksChanged)
    def sequenceBoundaryAnalysisRunning(self) -> bool:
        return sequence_boundary_analysis_running(self._session)

    @Property(str, notify=selectionChanged)
    def selectedClipId(self) -> str:
        return self._selected_clip_id()

    def _selected_clip_id(self) -> str:
        if self._session.state.selection.compound_id:
            return ""
        return self._session.state.selection.clip_ids[-1] if self._session.state.selection.clip_ids else ""

    @Property(list, notify=selectionChanged)
    def selectedClipIds(self) -> list[str]:
        return list(self._session.state.selection.clip_ids)

    @Property(str, notify=selectionChanged)
    def selectedCompoundId(self) -> str:
        return self._session.state.selection.compound_id

    @Property(str, notify=selectionChanged)
    def selectedTransitionId(self) -> str:
        return self._session.state.selection.transition_id

    @Property(str, notify=selectionChanged)
    def selectedMarkerId(self) -> str:
        return self._session.state.selection.marker_id

    @Property(str, notify=selectionChanged)
    def selectedRangeId(self) -> str:
        return self._session.state.selection.range_id

    @Property(int, notify=selectionChanged)
    def rangeInFrame(self) -> int:
        return (
            -1
            if self._session.state.selection.range_in_frame is None
            else self._session.state.selection.range_in_frame
        )

    @Property(dict, notify=selectionChanged)
    def selectedTransitionData(self) -> dict:
        row = self._session.models.transitions.findRow(
            "transitionId", self._session.state.selection.transition_id
        )
        return self._session.models.transitions.get(row)

    @Property(dict, notify=selectionChanged)
    def selectedClipData(self) -> dict:
        row = self._session.models.clips.findRow("clipId", self._selected_clip_id())
        return self._session.models.clips.get(row)

    @Property(list, notify=selectionChanged)
    def selectedClipReplacementOptions(self) -> list[dict]:
        clip_id = self._selected_clip_id()
        if not self._session.state.binding.current or not clip_id:
            return []
        row_index = self._session.models.clips.findRow("clipId", clip_id)
        row = self._session.models.clips.get(row_index)
        track_kind = str(row.get("trackKind", ""))
        options = []
        for asset in self._session.state.binding.require_current().list_assets():
            if asset.kind.value == "web" or asset.status.value != "online":
                continue
            compatible = (track_kind == TrackKind.VIDEO.value and asset.kind.value in {"video", "image"}) or (
                track_kind == TrackKind.AUDIO.value
                and asset.kind.value in {"video", "audio"}
                and asset.metadata.has_audio
            )
            if compatible:
                options.append({"label": asset.name, "value": asset.id})
        return options

    @Property(dict, notify=selectionChanged)
    def selectedClipsSummary(self) -> dict:
        rows = [
            self._session.models.clips.get(self._session.models.clips.findRow("clipId", clip_id))
            for clip_id in self._session.state.selection.clip_ids
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

    @Property(dict, notify=selectionChanged)
    def selectedCompoundData(self) -> dict:
        row = self._session.models.compound_clips.findRow(
            "compoundId", self._session.state.selection.compound_id
        )
        return self._session.models.compound_clips.get(row)

    @Property(bool, notify=selectionChanged)
    def canCreateCompoundClip(self) -> bool:
        if (
            not self._session.state.binding.timeline
            or self._session.state.selection.compound_id
            or len(self._session.state.selection.clip_ids) < 2
        ):
            return False
        selected_ids = set(self._session.state.selection.clip_ids)
        if any(
            selected_ids.intersection(item.clip_ids)
            for item in self._session.state.binding.require_timeline().state.compounds
        ):
            return False
        selected = sorted(
            (
                clip
                for clip in self._session.state.binding.require_timeline().state.clips
                if clip.id in selected_ids
            ),
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
        return bool(
            self._session.state.binding.timeline and self._session.state.binding.require_timeline().can_undo
        )

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:
        return bool(
            self._session.state.binding.timeline and self._session.state.binding.require_timeline().can_redo
        )

    @Slot(str)
    @Slot(str, bool)
    def selectClip(self, clip_id: str, toggle: bool = False) -> None:
        self._session.state.selection.compound_id = ""
        self._session.state.selection.clip_ids = self._session._updated_selection(
            self._session.state.selection.clip_ids,
            clip_id,
            toggle=toggle,
        )
        self._session.state.selection.transition_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str)
    def selectCompoundClip(self, compound_id: str) -> None:
        if not self._session.state.binding.timeline:
            return
        try:
            compound = next(
                item
                for item in self._session.state.binding.require_timeline().state.compounds
                if item.id == compound_id
            )
        except StopIteration:
            return
        self._session.state.selection.compound_id = compound.id
        self._session.state.selection.clip_ids = list(compound.clip_ids)
        self._session.state.selection.transition_id = ""
        self._session.state.selection.marker_id = ""
        self._session.state.selection.range_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str, result=bool)
    def isClipSelected(self, clip_id: str) -> bool:
        return clip_id in self._session.state.selection.clip_ids

    @Slot()
    def selectAllClips(self) -> None:
        self._session.state.selection.clip_ids = (
            [clip.id for clip in self._session.state.binding.require_timeline().state.clips]
            if self._session.state.binding.timeline
            else []
        )
        self._session.state.selection.compound_id = ""
        self._session.state.selection.transition_id = ""
        self._session.state.selection.marker_id = ""
        self._session.state.selection.range_id = ""
        self._session.updates.commit(selection=True)

    @Slot()
    def clearSelection(self) -> None:
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.state.selection.transition_id = ""
        self._session.state.selection.marker_id = ""
        self._session.state.selection.range_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str)
    def selectTransition(self, transition_id: str) -> None:
        self._session.state.selection.transition_id = transition_id
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str)
    def selectTimelineRange(self, range_id: str) -> None:
        self._session.state.selection.range_id = range_id
        self._session.updates.commit(selection=True)
