from __future__ import annotations

from collections.abc import Callable, Iterable

from mediaflow.application.edit_history import ProjectEditCommand, ProjectEditHistory
from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.application.timeline_diff import TimelineDiff
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import AssetKind, TrackKind, TransitionKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.project import ProjectProfile, SequenceInOut
from mediaflow.domain.timebase import (
    reframe_frames,
    source_frames_for_timeline_frames,
)
from mediaflow.domain.timeline import (
    Clip,
    ClipAudio,
    ClipTransform,
    TimelineMarker,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
    compatible_track_kinds,
)


class TimelineEditor:
    """Frame-accurate editing commands with a persisted command stack."""

    def __init__(
        self,
        repository: TimelineEditorDocuments,
        sequence_id: str,
        history: ProjectEditHistory | None = None,
    ):
        self.repository = repository
        self.sequence_id = sequence_id
        self._state = repository.load_timeline(sequence_id)
        self.history = history or ProjectEditHistory()

    @property
    def state(self) -> TimelineState:
        return self._snapshot(self._state)

    @property
    def can_undo(self) -> bool:
        return self.history.can_undo

    @property
    def can_redo(self) -> bool:
        return self.history.can_redo

    def reload(self) -> TimelineState:
        self._state = self.repository.load_timeline(self.sequence_id)
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
        if profile == old_profile and self._state.sequence.profile_confirmed:
            return self.state

        def reframe(value: int) -> int:
            return reframe_frames(value, old_profile, profile)

        def mutate(state: TimelineState) -> None:
            state.sequence = state.sequence.model_copy(
                update={"profile": profile, "profile_confirmed": True}
            )
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
                marker.model_copy(update={"frame": reframe(marker.frame)}) for marker in state.markers
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
            if state.sequence.in_out is not None:
                state.sequence = state.sequence.model_copy(
                    update={
                        "in_out": SequenceInOut(
                            in_frame=reframe(state.sequence.in_out.in_frame),
                            out_frame=max(
                                reframe(state.sequence.in_out.in_frame) + 1,
                                reframe(state.sequence.in_out.out_frame),
                            ),
                        )
                    }
                )

        self._commit("修改序列配置", mutate, allow_locked_changes=True)
        return self.state

    def set_sequence_in_out(self, in_frame: int, out_frame: int) -> TimelineState:
        duration = self._state.duration_frames
        if duration <= 0:
            raise ValueError("Sequence has no media to define in and out points")
        bounds = SequenceInOut(
            in_frame=max(0, min(duration - 1, in_frame)),
            out_frame=max(1, min(duration, out_frame)),
        )

        def mutate(state: TimelineState) -> None:
            state.sequence = state.sequence.model_copy(update={"in_out": bounds})

        self._commit("设置序列入出点", mutate, allow_locked_changes=True)
        return self.state

    def clear_sequence_in_out(self) -> TimelineState:
        def mutate(state: TimelineState) -> None:
            state.sequence = state.sequence.model_copy(update={"in_out": None})

        self._commit("清除序列入出点", mutate, allow_locked_changes=True)
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
                    previous.model_copy(update={"track_id": destination, "duration": overlap})
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

    def move_clips(
        self,
        clip_ids: Iterable[str],
        *,
        primary_clip_id: str,
        timeline_start: int,
        track_id: str,
        snap_targets: Iterable[int] = (),
        snap_tolerance_frames: int = 0,
    ) -> list[Clip]:
        selected_ids = list(dict.fromkeys(clip_ids))
        if primary_clip_id not in selected_ids:
            raise ValueError("Primary clip must be part of the selection")
        selected = [self._clip(clip_id) for clip_id in selected_ids]
        primary = self._clip(primary_clip_id)
        tracks = sorted(self._state.tracks, key=lambda item: item.position)
        track_positions = {item.id: index for index, item in enumerate(tracks)}
        if track_id not in track_positions:
            raise KeyError(track_id)
        frame_delta = (
            self.snap_frame(timeline_start, snap_targets, snap_tolerance_frames) - primary.timeline_start
        )
        track_delta = track_positions[track_id] - track_positions[primary.track_id]
        updates: dict[str, tuple[str, int]] = {}
        for clip in selected:
            destination_position = track_positions[clip.track_id] + track_delta
            if not 0 <= destination_position < len(tracks):
                raise ValueError("Selected clips cannot move outside the timeline tracks")
            destination = tracks[destination_position]
            asset = self.repository.get_asset(clip.asset_id)
            self._validate_asset_track(asset.kind, destination.kind)
            next_start = clip.timeline_start + frame_delta
            if next_start < 0:
                raise ValueError("Selected clips cannot move before the timeline start")
            updates[clip.id] = (destination.id, next_start)

        def mutate(state: TimelineState) -> None:
            state.clips = [
                clip.model_copy(
                    update={
                        "track_id": updates[clip.id][0],
                        "timeline_start": updates[clip.id][1],
                    }
                )
                if clip.id in updates
                else clip
                for clip in state.clips
            ]
            clips = {item.id: item for item in state.clips}
            state.transitions = [item for item in state.transitions if self._transition_is_valid(item, clips)]

        self._commit("移动多个片段", mutate)
        return [self._clip(clip_id) for clip_id in selected_ids]

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
            state.transitions = [item for item in state.transitions if self._transition_is_valid(item, clips)]

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
        self.delete_clips([clip_id], ripple=ripple)

    def delete_clips(self, clip_ids: Iterable[str], *, ripple: bool = False) -> None:
        selected_ids = set(clip_ids)
        if not selected_ids:
            return
        sources = [self._clip(clip_id) for clip_id in selected_ids]
        source_track_ids = {clip.track_id for clip in sources}

        def mutate(state: TimelineState) -> None:
            state.clips = [clip for clip in state.clips if clip.id not in selected_ids]
            state.transitions = [
                transition
                for transition in state.transitions
                if transition.left_clip_id not in selected_ids
                and transition.right_clip_id not in selected_ids
            ]
            if ripple:
                TimelineDiff.apply_ripple(
                    self._state,
                    state,
                    source_track_ids=source_track_ids,
                )

        label = "波纹删除多个片段" if ripple else "删除多个片段"
        if len(selected_ids) == 1:
            label = "波纹删除" if ripple else "删除片段"
        self._commit(label, mutate)

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
            index = next(index for index, item in enumerate(state.transitions) if item.id == transition_id)
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
            state.markers[index] = source.model_copy(update={"frame": frame, "name": name, "color": color})

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
        self.history.undo()
        return self.state

    def redo(self) -> TimelineState:
        self.history.redo()
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
        self._normalize_sequence_in_out(after)
        self._validate_timeline(after, allow_locked_changes=allow_locked_changes)
        if after == before:
            return
        self._persist_change(before, after)
        persisted = self.repository.load_timeline(self.sequence_id)
        self._state = persisted
        before_snapshot = self._snapshot(before)
        after_snapshot = self._snapshot(persisted)

        def restore(source: TimelineState, destination: TimelineState) -> None:
            self._persist_change(source, destination)
            self._state = self.repository.load_timeline(self.sequence_id)

        self.history.push(
            ProjectEditCommand(
                label=label,
                undo_action=lambda: restore(after_snapshot, before_snapshot),
                redo_action=lambda: restore(before_snapshot, after_snapshot),
            )
        )

    @staticmethod
    def _normalize_sequence_in_out(state: TimelineState) -> None:
        bounds = state.sequence.in_out
        if bounds is None:
            return
        duration = state.duration_frames
        if duration <= 0:
            state.sequence = state.sequence.model_copy(update={"in_out": None})
            return
        in_frame = min(bounds.in_frame, duration - 1)
        out_frame = min(bounds.out_frame, duration)
        state.sequence = state.sequence.model_copy(
            update={
                "in_out": (
                    SequenceInOut(in_frame=in_frame, out_frame=out_frame) if out_frame > in_frame else None
                )
            }
        )

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
                clip_id for clip_id, clip in after_clips.items() if clip != before_clips[clip_id]
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
            track_clips = state.clips_for_track(track.id)
            if (
                not allow_locked_changes
                and track.locked
                and self._state.clips_for_track(track.id) != track_clips
            ):
                raise PermissionError(f"Track is locked: {track.name}")
            previous: Clip | None = None
            for clip in track_clips:
                if clip.track_id not in tracks:
                    raise ValueError("Clip references an unknown track")
                if previous is not None and clip.timeline_start < previous.timeline_end:
                    raise ValueError(f"Clips cannot overlap on the same track: {track.name}")
                previous = clip
        clips_by_id = {clip.id: clip for clip in state.clips}
        for transition in state.transitions:
            if not self._transition_is_valid(transition, clips_by_id):
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
        if track_kind not in compatible_track_kinds(asset_kind):
            raise ValueError(f"Cannot place {asset_kind.value} on a {track_kind.value} track")

    @staticmethod
    def _track_label(kind: TrackKind) -> str:
        return {
            TrackKind.VIDEO: "视频",
            TrackKind.AUDIO: "音频",
            TrackKind.SUBTITLE: "字幕",
        }[kind]
