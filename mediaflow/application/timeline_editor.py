from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import AssetKind, TrackKind, TransitionKind
from mediaflow.domain.models import (
    Clip,
    ClipAudio,
    ClipTransform,
    ProjectProfile,
    TimelineMarker,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
    new_id,
)
from mediaflow.domain.timebase import (
    frames_to_seconds,
    seconds_to_frames,
    source_frames_for_timeline_frames,
)
from mediaflow.infrastructure.project_repository import ProjectRepository


@dataclass(slots=True)
class TimelineRevision:
    before: TimelineState
    after: TimelineState
    label: str


class TimelineEditor:
    """Frame-accurate editing commands with a persisted command stack."""

    def __init__(self, repository: ProjectRepository, sequence_id: str):
        self.repository = repository
        self.sequence_id = sequence_id
        self._state = repository.load_timeline(sequence_id)
        self._undo: list[TimelineRevision] = []
        self._redo: list[TimelineRevision] = []

    @property
    def state(self) -> TimelineState:
        return self._snapshot(self._state)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def reload(self) -> TimelineState:
        self._state = self.repository.load_timeline(self.sequence_id)
        self._undo.clear()
        self._redo.clear()
        return self.state

    def add_track(
        self,
        kind: TrackKind,
        name: str | None = None,
        *,
        audio_bus_id: str | None = None,
    ) -> Track:
        position = len(self._state.tracks)
        count = sum(track.kind == kind for track in self._state.tracks) + 1
        track = Track(
            sequence_id=self.sequence_id,
            name=name or f"{self._track_label(kind)} {count}",
            kind=kind,
            position=position,
            audio_bus_id=audio_bus_id,
        )

        def mutate(state: TimelineState) -> None:
            state.tracks.append(track)

        self._commit("添加轨道", mutate)
        return track

    def set_track_state(
        self,
        track_id: str,
        *,
        enabled: bool,
        locked: bool,
        muted: bool,
        solo: bool,
        audio_bus_id: str | None = None,
    ) -> Track:
        source = self._track(track_id)
        if source.kind == TrackKind.SUBTITLE and audio_bus_id is not None:
            raise ValueError("Subtitle tracks cannot route to an audio bus")
        if audio_bus_id is not None:
            buses = {bus.id for bus in self.repository.list_audio_buses(self.sequence_id)}
            if audio_bus_id not in buses:
                raise ValueError("Track audio bus does not belong to this sequence")

        def mutate(state: TimelineState) -> None:
            index = next(index for index, track in enumerate(state.tracks) if track.id == track_id)
            state.tracks[index] = state.tracks[index].model_copy(
                update={
                    "enabled": enabled,
                    "locked": locked,
                    "muted": muted,
                    "solo": solo,
                    "audio_bus_id": audio_bus_id,
                }
            )

        self._commit("调整轨道", mutate)
        return self._track(track_id)

    def move_track(self, track_id: str, position: int) -> Track:
        if not 0 <= position < len(self._state.tracks):
            raise ValueError("Track position is outside the timeline")

        def mutate(state: TimelineState) -> None:
            source_index = next(index for index, track in enumerate(state.tracks) if track.id == track_id)
            track = state.tracks.pop(source_index)
            state.tracks.insert(position, track)
            state.tracks = [
                item.model_copy(update={"position": index}) for index, item in enumerate(state.tracks)
            ]

        self._commit("排序轨道", mutate)
        return self._track(track_id)

    def set_sequence_profile(self, profile: ProjectProfile) -> TimelineState:
        old_profile = self._state.sequence.profile
        if profile == old_profile:
            return self.state

        def reframe(value: int) -> int:
            if (
                old_profile.fps_numerator == profile.fps_numerator
                and old_profile.fps_denominator == profile.fps_denominator
            ):
                return value
            return seconds_to_frames(
                frames_to_seconds(
                    value,
                    old_profile.fps_numerator,
                    old_profile.fps_denominator,
                ),
                profile.fps_numerator,
                profile.fps_denominator,
            )

        def mutate(state: TimelineState) -> None:
            state.sequence = state.sequence.model_copy(update={"profile": profile})
            state.clips = [
                clip.model_copy(
                    update={
                        "timeline_start": reframe(clip.timeline_start),
                        "source_in": reframe(clip.source_in),
                        "duration": max(
                            1,
                            reframe(clip.timeline_end) - reframe(clip.timeline_start),
                        ),
                        "audio": ClipAudio(
                            gain_db=clip.audio.gain_db,
                            pan=clip.audio.pan,
                            fade_in_frames=reframe(clip.audio.fade_in_frames),
                            fade_out_frames=reframe(clip.audio.fade_out_frames),
                        ),
                    }
                )
                for clip in state.clips
            ]
            state.transitions = [
                transition.model_copy(update={"duration": max(1, reframe(transition.duration))})
                for transition in state.transitions
            ]
            state.markers = [
                marker.model_copy(update={"frame": reframe(marker.frame)})
                for marker in state.markers
            ]
            state.ranges = [
                item.model_copy(
                    update={
                        "start_frame": reframe(item.start_frame),
                        "end_frame": max(reframe(item.start_frame) + 1, reframe(item.end_frame)),
                    }
                )
                for item in state.ranges
            ]

        self._commit("修改序列配置", mutate, allow_locked_changes=True)
        return self.state

    def add_clip(
        self,
        *,
        track_id: str,
        asset_id: str,
        timeline_start: int,
        source_in: int,
        duration: int,
        speed_numerator: int = 1,
        speed_denominator: int = 1,
        pitch_compensation: bool = True,
    ) -> Clip:
        track = self._track(track_id)
        asset = self.repository.get_asset(asset_id)
        self._validate_asset_track(asset.kind, track.kind)
        clip = Clip(
            track_id=track_id,
            asset_id=asset_id,
            timeline_start=timeline_start,
            source_in=source_in,
            duration=duration,
            speed_numerator=speed_numerator,
            speed_denominator=speed_denominator,
            pitch_compensation=pitch_compensation,
        )

        def mutate(state: TimelineState) -> None:
            state.clips.append(clip)

        self._commit("添加片段", mutate)
        return clip

    def move_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        track_id: str | None = None,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
        transition_from_overlap: bool = False,
    ) -> Clip:
        destination = track_id or self._clip(clip_id).track_id
        track = self._track(destination)
        source = self._clip(clip_id)
        asset = self.repository.get_asset(source.asset_id)
        self._validate_asset_track(asset.kind, track.kind)
        snapped = self.snap_frame(timeline_start, snap_targets, snap_tolerance_frames)
        overlap_transition: Transition | None = None
        if transition_from_overlap and track.kind == TrackKind.VIDEO:
            overlaps = [
                item
                for item in self._state.clips_for_track(destination)
                if item.id != clip_id
                and snapped < item.timeline_end
                and snapped + source.duration > item.timeline_start
            ]
            if len(overlaps) == 1:
                neighbor = overlaps[0]
                if snapped < neighbor.timeline_start:
                    overlap = snapped + source.duration - neighbor.timeline_start
                    snapped = neighbor.timeline_start - source.duration
                    left_id, right_id = source.id, neighbor.id
                else:
                    overlap = neighbor.timeline_end - snapped
                    snapped = neighbor.timeline_end
                    left_id, right_id = neighbor.id, source.id
                if snapped < 0 or overlap <= 0 or overlap > min(source.duration, neighbor.duration):
                    raise ValueError("Clip overlap cannot form a transition")
                previous = next(
                    (
                        item
                        for item in self._state.transitions
                        if item.left_clip_id == left_id and item.right_clip_id == right_id
                    ),
                    None,
                )
                overlap_transition = (
                    previous.model_copy(
                        update={"track_id": destination, "duration": overlap}
                    )
                    if previous
                    else Transition(
                        track_id=destination,
                        left_clip_id=left_id,
                        right_clip_id=right_id,
                        kind=TransitionKind.DISSOLVE,
                        duration=overlap,
                    )
                )

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={"track_id": destination, "timeline_start": snapped}
            )
            state.transitions = [
                item
                for item in state.transitions
                if item.left_clip_id != clip_id and item.right_clip_id != clip_id
            ]
            if overlap_transition is not None:
                state.transitions.append(overlap_transition)

        self._commit("移动片段并调整转场" if overlap_transition else "移动片段", mutate)
        return self._clip(clip_id)

    def copy_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        track_id: str | None = None,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
    ) -> Clip:
        source = self._clip(clip_id)
        destination = track_id or source.track_id
        track = self._track(destination)
        asset = self.repository.get_asset(source.asset_id)
        self._validate_asset_track(asset.kind, track.kind)
        snapped = self.snap_frame(timeline_start, snap_targets, snap_tolerance_frames)
        copied = source.model_copy(
            update={"id": new_id(), "track_id": destination, "timeline_start": snapped}
        )

        def mutate(state: TimelineState) -> None:
            state.clips.append(copied)

        self._commit("复制片段", mutate)
        return self._clip(copied.id)

    def trim_clip(
        self,
        clip_id: str,
        *,
        timeline_start: int,
        source_in: int,
        duration: int,
    ) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={
                    "timeline_start": timeline_start,
                    "source_in": source_in,
                    "duration": duration,
                }
            )
            clips = {item.id: item for item in state.clips}
            state.transitions = [
                item
                for item in state.transitions
                if self._transition_is_valid(item, clips)
            ]

        self._commit("裁剪片段", mutate)
        return self._clip(clip_id)

    def set_clip_transform(self, clip_id: str, transform: ClipTransform) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"transform": transform})

        self._commit("调整画面", mutate)
        return self._clip(clip_id)

    def set_clip_audio(self, clip_id: str, audio: ClipAudio) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"audio": audio})

        self._commit("调整片段音频", mutate)
        return self._clip(clip_id)

    def set_clip_speed(
        self,
        clip_id: str,
        *,
        speed_numerator: int,
        speed_denominator: int,
        pitch_compensation: bool,
    ) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            source = state.clips[index]
            source_in = source.source_in
            if (source.speed_numerator > 0) != (speed_numerator > 0):
                consumed = source_frames_for_timeline_frames(
                    source.duration,
                    source.speed_numerator,
                    source.speed_denominator,
                )
                source_in = (
                    source.source_in + consumed - 1
                    if speed_numerator < 0
                    else max(0, source.source_in - consumed + 1)
                )
            state.clips[index] = source.model_copy(
                update={
                    "source_in": source_in,
                    "speed_numerator": speed_numerator,
                    "speed_denominator": speed_denominator,
                    "pitch_compensation": pitch_compensation,
                }
            )

        self._commit("调整速度", mutate)
        return self._clip(clip_id)

    def split_clip(self, clip_id: str, split_frame: int) -> tuple[Clip, Clip]:
        source = self._clip(clip_id)
        if not source.timeline_start < split_frame < source.timeline_end:
            raise ValueError("Split frame must be inside the clip")
        left_duration = split_frame - source.timeline_start
        right_duration = source.duration - left_duration
        source_delta = source_frames_for_timeline_frames(
            left_duration,
            source.speed_numerator,
            source.speed_denominator,
        )
        right_source_in = (
            source.source_in + source_delta if source.speed_numerator > 0 else source.source_in - source_delta
        )
        if right_source_in < 0:
            raise ValueError("Reverse split exceeds the available source range")
        left = source.model_copy(update={"duration": left_duration})
        right = source.model_copy(
            update={
                "id": Clip(
                    track_id=source.track_id,
                    asset_id=source.asset_id,
                    timeline_start=split_frame,
                    source_in=right_source_in,
                    duration=right_duration,
                    speed_numerator=source.speed_numerator,
                    speed_denominator=source.speed_denominator,
                    pitch_compensation=source.pitch_compensation,
                    transform=source.transform,
                    audio=source.audio,
                ).id,
                "timeline_start": split_frame,
                "source_in": right_source_in,
                "duration": right_duration,
            }
        )

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = left
            state.clips.insert(index + 1, right)
            state.transitions = [
                transition
                for transition in state.transitions
                if transition.left_clip_id != clip_id and transition.right_clip_id != clip_id
            ]

        self._commit("分割片段", mutate)
        return self._clip(left.id), self._clip(right.id)

    def delete_clip(self, clip_id: str, *, ripple: bool = False) -> None:
        source = self._clip(clip_id)

        def mutate(state: TimelineState) -> None:
            state.clips = [clip for clip in state.clips if clip.id != clip_id]
            state.transitions = [
                transition
                for transition in state.transitions
                if transition.left_clip_id != clip_id and transition.right_clip_id != clip_id
            ]
            if ripple:
                for index, clip in enumerate(state.clips):
                    track = next(track for track in state.tracks if track.id == clip.track_id)
                    if not track.locked and clip.timeline_start >= source.timeline_end:
                        state.clips[index] = clip.model_copy(
                            update={"timeline_start": clip.timeline_start - source.duration}
                        )
                state.markers = [
                    marker.model_copy(
                        update={
                            "frame": (
                                marker.frame - source.duration
                                if marker.frame >= source.timeline_end
                                else source.timeline_start
                                if marker.frame >= source.timeline_start
                                else marker.frame
                            )
                        }
                    )
                    for marker in state.markers
                ]
                adjusted_ranges: list[TimelineRange] = []
                for item in state.ranges:
                    if item.end_frame <= source.timeline_start:
                        adjusted_ranges.append(item)
                        continue
                    if item.start_frame >= source.timeline_end:
                        adjusted_ranges.append(
                            item.model_copy(
                                update={
                                    "start_frame": item.start_frame - source.duration,
                                    "end_frame": item.end_frame - source.duration,
                                }
                            )
                        )
                        continue
                    start_frame = min(item.start_frame, source.timeline_start)
                    end_frame = (
                        item.end_frame - source.duration
                        if item.end_frame > source.timeline_end
                        else source.timeline_start
                    )
                    if end_frame > start_frame:
                        adjusted_ranges.append(
                            item.model_copy(
                                update={"start_frame": start_frame, "end_frame": end_frame}
                            )
                        )
                state.ranges = adjusted_ranges

        self._commit("波纹删除" if ripple else "删除片段", mutate)

    def create_transition(
        self,
        left_clip_id: str,
        right_clip_id: str,
        kind: TransitionKind,
        duration: int,
    ) -> Transition:
        left = self._clip(left_clip_id)
        right = self._clip(right_clip_id)
        if not transition_is_available(kind, self._state.sequence.profile.color_mode):
            raise ValueError("Transition is not verified for HDR10 projects")
        if left.track_id != right.track_id:
            raise ValueError("Transition clips must be on the same track")
        if left.timeline_end != right.timeline_start:
            raise ValueError("Transition clips must be adjacent")
        if duration > min(left.duration, right.duration):
            raise ValueError("Transition duration exceeds a source clip")
        transition = Transition(
            track_id=left.track_id,
            left_clip_id=left.id,
            right_clip_id=right.id,
            kind=kind,
            duration=duration,
        )

        def mutate(state: TimelineState) -> None:
            state.transitions = [
                item
                for item in state.transitions
                if not (item.left_clip_id == left.id and item.right_clip_id == right.id)
            ]
            state.transitions.append(transition)

        self._commit("添加转场", mutate)
        return transition

    def update_transition(
        self,
        transition_id: str,
        *,
        kind: TransitionKind,
        duration: int,
        parameters: dict | None = None,
    ) -> Transition:
        source = self._transition(transition_id)
        if not transition_is_available(kind, self._state.sequence.profile.color_mode):
            raise ValueError("Transition is not verified for HDR10 projects")
        left = self._clip(source.left_clip_id)
        right = self._clip(source.right_clip_id)
        if duration <= 0 or duration > min(left.duration, right.duration):
            raise ValueError("Transition duration exceeds the available clips")

        def mutate(state: TimelineState) -> None:
            index = next(
                index for index, item in enumerate(state.transitions) if item.id == transition_id
            )
            state.transitions[index] = source.model_copy(
                update={
                    "kind": kind,
                    "duration": duration,
                    "parameters": source.parameters if parameters is None else parameters,
                }
            )

        self._commit("调整转场", mutate)
        return self._transition(transition_id)

    def remove_transition(self, transition_id: str) -> None:
        self._transition(transition_id)

        def mutate(state: TimelineState) -> None:
            state.transitions = [item for item in state.transitions if item.id != transition_id]

        self._commit("移除转场", mutate)

    def add_marker(self, frame: int, name: str = "", color: str = "#4ea1ff") -> TimelineMarker:
        marker = TimelineMarker(
            sequence_id=self.sequence_id,
            frame=frame,
            name=name,
            color=color,
        )

        def mutate(state: TimelineState) -> None:
            state.markers.append(marker)

        self._commit("添加标记", mutate)
        return self._marker(marker.id)

    def update_marker(
        self,
        marker_id: str,
        *,
        frame: int,
        name: str,
        color: str,
    ) -> TimelineMarker:
        source = self._marker(marker_id)

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.markers) if item.id == marker_id)
            state.markers[index] = source.model_copy(
                update={"frame": frame, "name": name, "color": color}
            )

        self._commit("调整标记", mutate)
        return self._marker(marker_id)

    def remove_marker(self, marker_id: str) -> None:
        self._marker(marker_id)

        def mutate(state: TimelineState) -> None:
            state.markers = [item for item in state.markers if item.id != marker_id]

        self._commit("删除标记", mutate)

    def add_range(
        self,
        start_frame: int,
        end_frame: int,
        name: str = "",
        color: str = "#4ea1ff",
    ) -> TimelineRange:
        item = TimelineRange(
            sequence_id=self.sequence_id,
            start_frame=start_frame,
            end_frame=end_frame,
            name=name,
            color=color,
        )

        def mutate(state: TimelineState) -> None:
            state.ranges.append(item)

        self._commit("添加范围", mutate)
        return self._range(item.id)

    def update_range(
        self,
        range_id: str,
        *,
        start_frame: int,
        end_frame: int,
        name: str,
        color: str,
    ) -> TimelineRange:
        source = self._range(range_id)

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.ranges) if item.id == range_id)
            state.ranges[index] = source.model_copy(
                update={
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "name": name,
                    "color": color,
                }
            )

        self._commit("调整范围", mutate)
        return self._range(range_id)

    def remove_range(self, range_id: str) -> None:
        self._range(range_id)

        def mutate(state: TimelineState) -> None:
            state.ranges = [item for item in state.ranges if item.id != range_id]

        self._commit("删除范围", mutate)

    def undo(self) -> TimelineState:
        if not self._undo:
            raise RuntimeError("Nothing to undo")
        revision = self._undo.pop()
        self._persist_change(revision.after, revision.before)
        self._state = self._snapshot(revision.before)
        self._redo.append(revision)
        return self.state

    def redo(self) -> TimelineState:
        if not self._redo:
            raise RuntimeError("Nothing to redo")
        revision = self._redo.pop()
        self._persist_change(revision.before, revision.after)
        self._state = self._snapshot(revision.after)
        self._undo.append(revision)
        return self.state

    @staticmethod
    def snap_frame(frame: int, targets: Iterable[int], tolerance_frames: int) -> int:
        if frame < 0:
            raise ValueError("Timeline frame cannot be negative")
        if tolerance_frames < 0:
            raise ValueError("Snap tolerance cannot be negative")
        candidates = [target for target in targets if abs(target - frame) <= tolerance_frames]
        if not candidates:
            return frame
        return min(candidates, key=lambda target: (abs(target - frame), target))

    def _commit(
        self,
        label: str,
        mutate: Callable[[TimelineState], None],
        *,
        allow_locked_changes: bool = False,
    ) -> None:
        before = self._snapshot(self._state)
        after = self._snapshot(before)
        mutate(after)
        self._validate_timeline(after, allow_locked_changes=allow_locked_changes)
        if after == before:
            return
        self._persist_change(before, after)
        persisted = self.repository.load_timeline(self.sequence_id)
        self._undo.append(TimelineRevision(before=before, after=persisted, label=label))
        self._redo.clear()
        self._state = persisted

    @staticmethod
    def _snapshot(state: TimelineState) -> TimelineState:
        # Editing commands replace validated domain objects instead of mutating
        # them in place. Copying the four containers is therefore a complete
        # session snapshot without recursively cloning hundreds of clips.
        return state.model_copy(
            update={
                "tracks": list(state.tracks),
                "clips": list(state.clips),
                "transitions": list(state.transitions),
                "markers": list(state.markers),
                "ranges": list(state.ranges),
            }
        )

    def _persist_change(self, before: TimelineState, after: TimelineState) -> None:
        before_clips = {clip.id: clip for clip in before.clips}
        after_clips = {clip.id: clip for clip in after.clips}
        graph_is_unchanged = (
            before.sequence == after.sequence
            and before.tracks == after.tracks
            and before.transitions == after.transitions
            and before.markers == after.markers
            and before.ranges == after.ranges
            and set(before_clips) == set(after_clips)
        )
        if graph_is_unchanged:
            changed_clip_ids = {
                clip_id
                for clip_id, clip in after_clips.items()
                if clip != before_clips[clip_id]
            }
            self.repository.save_clip_changes(after, changed_clip_ids)
            return
        self.repository.save_timeline(after)

    def _validate_timeline(self, state: TimelineState, *, allow_locked_changes: bool = False) -> None:
        tracks = {track.id: track for track in state.tracks}
        if len({track.position for track in state.tracks}) != len(state.tracks):
            raise ValueError("Track positions must be unique")
        if any(clip.track_id not in tracks for clip in state.clips):
            raise ValueError("Clip references an unknown track")
        if any(marker.sequence_id != state.sequence.id for marker in state.markers):
            raise ValueError("Marker references another sequence")
        if any(item.sequence_id != state.sequence.id for item in state.ranges):
            raise ValueError("Range references another sequence")
        if len({marker.id for marker in state.markers}) != len(state.markers):
            raise ValueError("Marker identifiers must be unique")
        if len({item.id for item in state.ranges}) != len(state.ranges):
            raise ValueError("Range identifiers must be unique")
        for track in state.tracks:
            clips = state.clips_for_track(track.id)
            if not allow_locked_changes and track.locked and self._state.clips_for_track(track.id) != clips:
                raise PermissionError(f"Track is locked: {track.name}")
            previous: Clip | None = None
            for clip in clips:
                if clip.track_id not in tracks:
                    raise ValueError("Clip references an unknown track")
                if previous is not None and clip.timeline_start < previous.timeline_end:
                    raise ValueError(f"Clips cannot overlap on the same track: {track.name}")
                previous = clip
        clips = {clip.id: clip for clip in state.clips}
        for transition in state.transitions:
            if not self._transition_is_valid(transition, clips):
                raise ValueError("Transition references clips that are no longer adjacent")

    def _track(self, track_id: str) -> Track:
        try:
            return next(track for track in self._state.tracks if track.id == track_id)
        except StopIteration as error:
            raise KeyError(track_id) from error

    def _clip(self, clip_id: str) -> Clip:
        try:
            return next(clip for clip in self._state.clips if clip.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error

    def _transition(self, transition_id: str) -> Transition:
        try:
            return next(item for item in self._state.transitions if item.id == transition_id)
        except StopIteration as error:
            raise KeyError(transition_id) from error

    def _marker(self, marker_id: str) -> TimelineMarker:
        try:
            return next(item for item in self._state.markers if item.id == marker_id)
        except StopIteration as error:
            raise KeyError(marker_id) from error

    def _range(self, range_id: str) -> TimelineRange:
        try:
            return next(item for item in self._state.ranges if item.id == range_id)
        except StopIteration as error:
            raise KeyError(range_id) from error

    @staticmethod
    def _transition_is_valid(transition: Transition, clips: dict[str, Clip]) -> bool:
        left = clips.get(transition.left_clip_id)
        right = clips.get(transition.right_clip_id)
        return bool(
            left
            and right
            and left.track_id == transition.track_id == right.track_id
            and left.timeline_end == right.timeline_start
            and transition.duration <= min(left.duration, right.duration)
        )

    @staticmethod
    def _clip_index(state: TimelineState, clip_id: str) -> int:
        try:
            return next(index for index, clip in enumerate(state.clips) if clip.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error

    @staticmethod
    def _validate_asset_track(asset_kind: AssetKind, track_kind: TrackKind) -> None:
        compatible = {
            TrackKind.VIDEO: {AssetKind.VIDEO, AssetKind.IMAGE},
            TrackKind.AUDIO: {AssetKind.VIDEO, AssetKind.AUDIO},
            TrackKind.SUBTITLE: {AssetKind.SUBTITLE},
        }
        if asset_kind not in compatible[track_kind]:
            raise ValueError(f"Cannot place {asset_kind.value} on a {track_kind.value} track")

    @staticmethod
    def _track_label(kind: TrackKind) -> str:
        return {
            TrackKind.VIDEO: "视频",
            TrackKind.AUDIO: "音频",
            TrackKind.SUBTITLE: "字幕",
        }[kind]
