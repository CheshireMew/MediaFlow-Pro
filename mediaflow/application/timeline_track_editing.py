from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.project import ProjectProfile, SequenceInOut
from mediaflow.domain.timeline import TimelineState, Track


class TimelineTrackEditing:
    repository: TimelineEditorDocuments
    sequence_id: str
    _changes: TimelineChangeSession

    if TYPE_CHECKING:
        @property
        def state(self) -> TimelineState: ...

        def _commit(
            self,
            label: str,
            mutate: Callable[[TimelineState], None],
            *,
            allow_locked_changes: bool = False,
        ) -> None: ...

        def _track(self, track_id: str) -> Track: ...
    def add_track(
        self,
        kind: TrackKind,
        name: str | None = None,
        *,
        audio_bus_id: str | None = None,
        position: int | None = None,
    ) -> Track:
        insert_position = len(self._changes.current.tracks) if position is None else position
        if not 0 <= insert_position <= len(self._changes.current.tracks):
            raise ValueError("Track position is outside the timeline")
        count = sum(track.kind == kind for track in self._changes.current.tracks) + 1
        track = Track(
            sequence_id=self.sequence_id,
            name=name or f"{TimelineRules.track_label(kind)} {count}",
            kind=kind,
            position=insert_position,
            audio_bus_id=audio_bus_id,
        )

        def mutate(state: TimelineState) -> None:
            state.tracks.insert(insert_position, track)
            TimelineRules.renumber_tracks(state)

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
            buses = {bus.id for bus in self.repository.audio.list_audio_buses(self.sequence_id)}
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

    def set_subtitle_track_style(
        self,
        track_id: str,
        style: SubtitleStyle,
    ) -> Track:
        source = self._track(track_id)
        if source.kind != TrackKind.SUBTITLE:
            raise ValueError("Subtitle styles can only be assigned to subtitle tracks")

        def mutate(state: TimelineState) -> None:
            state.tracks = [
                track.model_copy(update={"subtitle_style": style}) if track.id == track_id else track
                for track in state.tracks
            ]

        self._commit("调整字幕轨样式", mutate)
        return self._track(track_id)

    def set_primary_dialogue_track(self, track_id: str) -> Track:
        source = self._track(track_id)
        if source.kind != TrackKind.AUDIO:
            raise ValueError("主要对白只能指定到音频轨")

        def mutate(state: TimelineState) -> None:
            state.tracks = [
                track.model_copy(update={"primary_dialogue": track.id == track_id})
                if track.kind == TrackKind.AUDIO
                else track
                for track in state.tracks
            ]

        self._commit("指定主要对白轨", mutate)
        return self._track(track_id)

    def move_track(self, track_id: str, position: int) -> Track:
        if not 0 <= position < len(self._changes.current.tracks):
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
        return self._changes.set_sequence_profile(profile)

    def set_sequence_in_out(self, in_frame: int, out_frame: int) -> TimelineState:
        duration = self._changes.current.duration_frames
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
