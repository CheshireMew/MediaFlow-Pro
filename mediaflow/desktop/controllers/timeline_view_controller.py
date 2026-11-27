from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.desktop.presentation_timeline import transition_options
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import ColorMode, TrackKind, TransitionKind
from mediaflow.domain.timeline import Transition

from .controller_facet import ControllerFacet
from .timeline_controller_scopes import TimelineViewScope
from .timeline_selection import sequence_boundary_analysis_running

FILMSTRIP_BASE_IDLE_MS = 120
FILMSTRIP_MAX_IDLE_MS = 2_500
FILMSTRIP_CLIPS_PER_ADDED_MS = 2


def filmstrip_idle_delay_ms(clip_count: int) -> int:
    return min(
        FILMSTRIP_MAX_IDLE_MS,
        FILMSTRIP_BASE_IDLE_MS + max(0, int(clip_count)) // FILMSTRIP_CLIPS_PER_ADDED_MS,
    )


class TimelineViewController(ControllerFacet[TimelineViewScope]):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    previewRangeRequested = Signal(int, int)

    @Property(QObject, constant=True)
    def tracksModel(self) -> QObject:
        return self._session.tracks_model

    @Property(QObject, constant=True)
    def clipsModel(self) -> QObject:
        return self._session.clips_model

    @Property(int, notify=projectStateChanged)
    def filmstripIdleDelayMs(self) -> int:
        return filmstrip_idle_delay_ms(self._session.clips_model.rowCount())

    @Slot(float, float, float, int)
    def requestFilmstrip(
        self,
        visible_start_frame: float,
        visible_end_frame: float,
        pixels_per_frame: float,
        height: int,
    ) -> None:
        if not self._session.project_binding.current or not self._session.project_binding.active_sequence_id:
            return
        self._session.request_state.filmstrip_id += 1
        filmstrip_generation = self._session.request_state.filmstrip_id
        request_id = (
            self._session.project_binding.generation,
            filmstrip_generation,
            self._session.project_binding.active_sequence_id,
        )
        previous = self._session.request_state.filmstrip_future
        if previous is not None and not previous.done():
            previous.cancel()
        project_dir = self._session.project_binding.require_current().project_dir
        sequence_id = self._session.project_binding.active_sequence_id
        request_owner = self._session.project_binding.require_current().actor_id
        self._session.request_state.filmstrip_future = self._session.background.submit(
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
        return self._session.compound_clips_model

    @Property(QObject, constant=True)
    def transitionsModel(self) -> QObject:
        return self._session.transitions_model

    @Property(QObject, constant=True)
    def timelineMarkersModel(self) -> QObject:
        return self._session.markers_model

    @Property(QObject, constant=True)
    def timelineRangesModel(self) -> QObject:
        return self._session.ranges_model

    @Property(list, notify=projectStateChanged)
    def transitionOptions(self) -> list[dict]:
        color_mode = (
            self._session.project_binding.require_timeline().state.sequence.profile.color_mode
            if self._session.project_binding.timeline
            else ColorMode.SDR_BT709
        )
        return transition_options(color_mode)

    @Slot(str, str, int)
    def previewTransitionAfter(self, clip_id: str, kind: str, duration: int) -> None:
        """Compile a temporary transition through the real MLT preview graph."""
        try:
            if (
                not self._session.project_binding.current
                or not self._session.project_binding.timeline
                or not clip_id
            ):
                return
            state = self._session.project_binding.require_timeline().state.model_copy(deep=True)
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
                self._session.project_binding.require_current().project_dir,
                state,
                use_proxies=self._session.read_service_settings().preview.preview_quality != "source",
                prefer_sdr_preview_proxy=(
                    state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ
                    and not self._session.presentation_state.hdr_preview_active
                ),
            )
            self._session.presentation_state.preview_graph_path = str(path)
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
            self._session.project_binding.timeline
            and self._session.project_binding.require_timeline().state.clips
        ):
            self._session.preview.schedule()

    @Property(bool, notify=tasksChanged)
    def sequenceBoundaryAnalysisRunning(self) -> bool:
        return sequence_boundary_analysis_running(
            self._session.project_binding, self._session.task_view_state
        )

    @Property(str, notify=selectionChanged)
    def selectedClipId(self) -> str:
        return self._selected_clip_id()

    def _selected_clip_id(self) -> str:
        if self._session.read_selection().compound_id:
            return ""
        return self._session.read_selection().clip_ids[-1] if self._session.read_selection().clip_ids else ""

    @Property(list, notify=selectionChanged)
    def selectedClipIds(self) -> list[str]:
        return list(self._session.read_selection().clip_ids)

    @Property(str, notify=selectionChanged)
    def selectedCompoundId(self) -> str:
        return self._session.read_selection().compound_id

    @Property(str, notify=selectionChanged)
    def selectedTransitionId(self) -> str:
        return self._session.read_selection().transition_id

    @Property(str, notify=selectionChanged)
    def selectedMarkerId(self) -> str:
        return self._session.read_selection().marker_id

    @Property(str, notify=selectionChanged)
    def selectedRangeId(self) -> str:
        return self._session.read_selection().range_id

    @Property(int, notify=selectionChanged)
    def rangeInFrame(self) -> int:
        selection = self._session.read_selection()
        return -1 if selection.range_in_frame is None else selection.range_in_frame

    @Property(dict, notify=selectionChanged)
    def selectedTransitionData(self) -> dict:
        row = self._session.transitions_model.findRow(
            "transitionId", self._session.read_selection().transition_id
        )
        return self._session.transitions_model.get(row)

    @Property(dict, notify=selectionChanged)
    def selectedClipData(self) -> dict:
        row = self._session.clips_model.findRow("clipId", self._selected_clip_id())
        return self._session.clips_model.get(row)

    @Property(list, notify=selectionChanged)
    def selectedClipReplacementOptions(self) -> list[dict]:
        clip_id = self._selected_clip_id()
        if not self._session.project_binding.current or not clip_id:
            return []
        row_index = self._session.clips_model.findRow("clipId", clip_id)
        row = self._session.clips_model.get(row_index)
        track_kind = str(row.get("trackKind", ""))
        options = []
        for asset in self._session.project_binding.require_current().list_assets():
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
            self._session.clips_model.get(self._session.clips_model.findRow("clipId", clip_id))
            for clip_id in self._session.read_selection().clip_ids
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
        row = self._session.compound_clips_model.findRow(
            "compoundId", self._session.read_selection().compound_id
        )
        return self._session.compound_clips_model.get(row)

    @Property(bool, notify=selectionChanged)
    def canCreateCompoundClip(self) -> bool:
        if (
            not self._session.project_binding.timeline
            or self._session.read_selection().compound_id
            or len(self._session.read_selection().clip_ids) < 2
        ):
            return False
        selected_ids = set(self._session.read_selection().clip_ids)
        if any(
            selected_ids.intersection(item.clip_ids)
            for item in self._session.project_binding.require_timeline().state.compounds
        ):
            return False
        selected = sorted(
            (
                clip
                for clip in self._session.project_binding.require_timeline().state.clips
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
            self._session.project_binding.timeline
            and self._session.project_binding.require_timeline().can_undo
        )

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:
        return bool(
            self._session.project_binding.timeline
            and self._session.project_binding.require_timeline().can_redo
        )

    @Slot(str)
    @Slot(str, bool)
    def selectClip(self, clip_id: str, toggle: bool = False) -> None:
        self._session.read_selection().compound_id = ""
        self._session.read_selection().clip_ids = self._session._updated_selection(
            self._session.read_selection().clip_ids,
            clip_id,
            toggle=toggle,
        )
        self._session.read_selection().transition_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str)
    def selectCompoundClip(self, compound_id: str) -> None:
        if not self._session.project_binding.timeline:
            return
        try:
            compound = next(
                item
                for item in self._session.project_binding.require_timeline().state.compounds
                if item.id == compound_id
            )
        except StopIteration:
            return
        self._session.read_selection().compound_id = compound.id
        self._session.read_selection().clip_ids = list(compound.clip_ids)
        self._session.read_selection().transition_id = ""
        self._session.read_selection().marker_id = ""
        self._session.read_selection().range_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str, result=bool)
    def isClipSelected(self, clip_id: str) -> bool:
        return clip_id in self._session.read_selection().clip_ids

    @Slot()
    def selectAllClips(self) -> None:
        self._session.read_selection().clip_ids = (
            [clip.id for clip in self._session.project_binding.require_timeline().state.clips]
            if self._session.project_binding.timeline
            else []
        )
        self._session.read_selection().compound_id = ""
        self._session.read_selection().transition_id = ""
        self._session.read_selection().marker_id = ""
        self._session.read_selection().range_id = ""
        self._session.updates.commit(selection=True)

    @Slot()
    def clearSelection(self) -> None:
        self._session.read_selection().clip_ids = []
        self._session.read_selection().compound_id = ""
        self._session.read_selection().transition_id = ""
        self._session.read_selection().marker_id = ""
        self._session.read_selection().range_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str)
    def selectTransition(self, transition_id: str) -> None:
        self._session.read_selection().transition_id = transition_id
        self._session.read_selection().clip_ids = []
        self._session.read_selection().compound_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str)
    def selectTimelineMarker(self, marker_id: str) -> None:
        self._session.read_selection().marker_id = marker_id
        self._session.read_selection().range_id = ""
        self._session.updates.commit(selection=True)

    @Slot(str)
    def selectTimelineRange(self, range_id: str) -> None:
        self._session.read_selection().marker_id = ""
        self._session.read_selection().range_id = range_id
        self._session.updates.commit(selection=True)
