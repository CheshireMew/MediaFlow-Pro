from __future__ import annotations

from dataclasses import dataclass

from mediaflow.application.ports import SequenceServiceDocuments
from mediaflow.application.timeline_clock import (
    project_frame_profile,
    reframe_timeline_clock,
)
from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.enums import AudioEffectKind, TrackKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.project import Sequence
from mediaflow.domain.subtitles import SubtitlePlacement
from mediaflow.domain.timebase import (
    reframe_frames,
    source_frames_for_timeline_frames,
)
from mediaflow.domain.timeline import (
    Clip,
    CompoundClip,
    TimelineMarker,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
)


@dataclass(frozen=True, slots=True)
class PreparedShortSequence:
    state: TimelineState
    audio_buses: tuple[AudioBus, ...]
    audio_effects: tuple[AudioEffect, ...]
    subtitle_placements: tuple[SubtitlePlacement, ...]
    new_sequence: bool


class SequenceService:
    def __init__(self, repository: SequenceServiceDocuments):
        self.repository = repository

    def create_short_from_range(
        self,
        source_sequence_id: str,
        range_id: str,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_range(
                source_sequence_id,
                range_id,
                name=name,
            )
        )

    def create_short_from_bounds(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_bounds(
                source_sequence_id,
                start_frame,
                end_frame,
                name=name,
            )
        )

    def sync_short_from_bounds(
        self,
        source_sequence_id: str,
        short_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_bounds(
                source_sequence_id,
                start_frame,
                end_frame,
                name=name,
                destination_sequence=(
                    self.repository.catalog.get_sequence(short_sequence_id)
                ),
            )
        )

    def prepare_short_from_range(
        self,
        source_sequence_id: str,
        range_id: str,
        *,
        name: str | None = None,
    ) -> PreparedShortSequence:
        source = self.repository.timeline.load_timeline(source_sequence_id)
        try:
            selected = next(item for item in source.ranges if item.id == range_id)
        except StopIteration as error:
            raise KeyError(range_id) from error
        return self._prepare_copy_selection(source, selected, name=name)

    def prepare_short_from_bounds(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
        destination_sequence: Sequence | None = None,
    ) -> PreparedShortSequence:
        source, selected = self._bounded_selection(
            source_sequence_id,
            start_frame,
            end_frame,
            name=name,
        )
        return self._prepare_copy_selection(
                source,
                selected,
                name=name,
                destination_sequence=destination_sequence,
            )

    def _bounded_selection(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None,
    ) -> tuple[TimelineState, TimelineRange]:
        source = self.repository.timeline.load_timeline(source_sequence_id)
        start = max(0, int(start_frame))
        end = min(source.duration_frames, int(end_frame))
        if end <= start:
            raise ValueError("短视频区间必须落在源时间轴内")
        return source, TimelineRange(
            sequence_id=source_sequence_id,
            start_frame=start,
            end_frame=end,
            name=(name or "短视频").strip() or "短视频",
        )

    def _prepare_copy_selection(
        self,
        source: TimelineState,
        selected: TimelineRange,
        *,
        name: str | None,
        destination_sequence: Sequence | None = None,
    ) -> PreparedShortSequence:
        new_sequence = destination_sequence is None
        destination_sequence = (
            destination_sequence
            or self.repository.catalog.prepare_short_sequence(
                name or selected.name or "短视频"
            )
        )
        destination = TimelineState(sequence=destination_sequence)
        source_profile = source.sequence.profile
        destination_profile = destination.sequence.profile
        destination_name = (name or selected.name or destination.sequence.name).strip()
        destination.sequence = destination.sequence.model_copy(
            update={
                "name": destination_name or destination.sequence.name,
                "profile": source_profile,
                "in_out": None,
            }
        )

        source_buses = self.repository.audio.list_audio_buses(source.sequence.id)
        bus_map = {bus.id: new_id() for bus in source_buses}
        cloned_buses = [
            AudioBus(
                id=bus_map[bus.id],
                sequence_id=destination_sequence.id,
                name=bus.name,
                parent_bus_id=(
                    bus_map[bus.parent_bus_id]
                    if bus.parent_bus_id is not None
                    else None
                ),
                position=bus.position,
                gain_db=bus.gain_db,
                muted=bus.muted,
                solo=bus.solo,
                channel_layout=bus.channel_layout,
            )
            for bus in source_buses
        ]
        cloned_effects: list[AudioEffect] = []
        for bus in source_buses:
            for effect in self.repository.audio.list_audio_effects(bus.id):
                parameters = dict(effect.parameters)
                if effect.kind == AudioEffectKind.DUCKING:
                    driver_bus_id = str(parameters.get("driver_bus_id", ""))
                    if driver_bus_id:
                        if driver_bus_id not in bus_map:
                            raise ValueError(
                                "源序列的闪避效果引用了不存在的音频总线"
                            )
                        parameters["driver_bus_id"] = bus_map[driver_bus_id]
                cloned_effects.append(
                    AudioEffect(
                        id=new_id(),
                        bus_id=bus_map[bus.id],
                        kind=effect.kind,
                        position=effect.position,
                        enabled=effect.enabled,
                        parameters=parameters,
                    )
                )

        track_map: dict[str, str] = {}
        destination.tracks = []
        for position, track in enumerate(sorted(source.tracks, key=lambda item: item.position)):
            audio_bus_id = (
                bus_map[track.audio_bus_id]
                if track.audio_bus_id is not None
                else None
            )
            copied_track = Track(
                sequence_id=destination_sequence.id,
                name=track.name,
                kind=track.kind,
                position=position,
                enabled=track.enabled,
                locked=False,
                muted=track.muted,
                solo=track.solo,
                audio_bus_id=audio_bus_id,
                primary_dialogue=track.primary_dialogue,
            )
            destination.tracks.append(copied_track)
            track_map[track.id] = copied_track.id
        destination.tracks = [
            track.model_copy(
                update={
                    "linked_audio_track_id": (
                        track_map[source_track.linked_audio_track_id]
                        if source_track.linked_audio_track_id in track_map
                        else None
                    )
                }
            )
            for track, source_track in zip(
                destination.tracks,
                sorted(source.tracks, key=lambda item: item.position),
                strict=True,
            )
        ]

        clip_map: dict[str, str] = {}
        destination.clips = []
        for clip in source.clips:
            overlap_start = max(selected.start_frame, clip.timeline_start)
            overlap_end = min(selected.end_frame, clip.timeline_end)
            if overlap_end <= overlap_start:
                continue
            source_delta = source_frames_for_timeline_frames(
                overlap_start - clip.timeline_start,
                clip.speed_numerator,
                clip.speed_denominator,
            )
            source_in = (
                clip.source_in + source_delta if clip.speed_numerator > 0 else clip.source_in - source_delta
            )
            timeline_start = overlap_start - selected.start_frame
            timeline_end = overlap_end - selected.start_frame
            copied = Clip(
                id=new_id(),
                track_id=track_map[clip.track_id],
                asset_id=clip.asset_id,
                timeline_start=timeline_start,
                source_in=source_in,
                duration=max(1, timeline_end - timeline_start),
                media_kind=clip.media_kind,
                speed_numerator=clip.speed_numerator,
                speed_denominator=clip.speed_denominator,
                pitch_compensation=clip.pitch_compensation,
                transform=clip.transform,
                transform_keyframes=list(clip.transform_keyframes),
                audio=clip.audio,
            )
            destination.clips.append(copied)
            clip_map[clip.id] = copied.id

        destination.web_states = {
            destination_clip_id: source.web_states[source_clip_id].model_copy(
                update={"clip_id": destination_clip_id, "revision": 0}
            )
            for source_clip_id, destination_clip_id in clip_map.items()
            if source_clip_id in source.web_states
        }

        destination.compounds = [
            CompoundClip(
                sequence_id=destination_sequence.id,
                name=item.name,
                clip_ids=[clip_map[clip_id] for clip_id in item.clip_ids],
            )
            for item in source.compounds
            if all(clip_id in clip_map for clip_id in item.clip_ids)
        ]

        destination.transitions = []
        for item in source.transitions:
            if item.left_clip_id not in clip_map or item.right_clip_id not in clip_map:
                continue
            left = next(clip for clip in destination.clips if clip.id == clip_map[item.left_clip_id])
            right = next(clip for clip in destination.clips if clip.id == clip_map[item.right_clip_id])
            if left.timeline_end != right.timeline_start:
                continue
            destination.transitions.append(
                Transition(
                    track_id=track_map[item.track_id],
                    left_clip_id=left.id,
                    right_clip_id=right.id,
                    kind=item.kind,
                    duration=min(
                        left.duration,
                        right.duration,
                        item.duration,
                    ),
                    parameters=item.parameters,
                )
            )

        destination.markers = [
            TimelineMarker(
                sequence_id=destination_sequence.id,
                frame=item.frame - selected.start_frame,
                name=item.name,
                color=item.color,
            )
            for item in source.markers
            if selected.start_frame <= item.frame < selected.end_frame
        ]
        destination.ranges = []
        for range_item in source.ranges:
            start = max(range_item.start_frame, selected.start_frame)
            end = min(range_item.end_frame, selected.end_frame)
            if end <= start or range_item.id == selected.id:
                continue
            converted_start = start - selected.start_frame
            destination.ranges.append(
                TimelineRange(
                    sequence_id=destination_sequence.id,
                    start_frame=converted_start,
                    end_frame=max(
                        converted_start + 1,
                        end - selected.start_frame,
                    ),
                    name=range_item.name,
                    color=range_item.color,
                )
            )
        destination = reframe_timeline_clock(
            destination,
            self.repository.catalog.list_assets(),
            destination_profile,
            asset_source_profile=project_frame_profile(self.repository.catalog),
        ).state
        placements: list[SubtitlePlacement] = []
        for source_track_id, destination_track_id in track_map.items():
            source_track = next(item for item in source.tracks if item.id == source_track_id)
            if source_track.kind != TrackKind.SUBTITLE:
                continue
            for placement in self.repository.subtitles.list_subtitle_placements(source_track_id):
                start = max(placement.start_frame, selected.start_frame)
                end = min(placement.end_frame, selected.end_frame)
                if end <= start:
                    continue
                converted_start = reframe_frames(
                    start - selected.start_frame,
                    source_profile,
                    destination_profile,
                )
                placements.append(
                    SubtitlePlacement(
                        track_id=destination_track_id,
                        segment_id=placement.segment_id,
                        clip_id=clip_map.get(placement.clip_id or ""),
                        start_frame=converted_start,
                        end_frame=max(
                            converted_start + 1,
                            reframe_frames(
                                end - selected.start_frame,
                                source_profile,
                                destination_profile,
                            ),
                        ),
                        text_override=placement.text_override,
                        timing_overridden=placement.timing_overridden,
                    )
                )
        return PreparedShortSequence(
            state=destination,
            audio_buses=tuple(cloned_buses),
            audio_effects=tuple(cloned_effects),
            subtitle_placements=tuple(placements),
            new_sequence=new_sequence,
        )

    def commit_prepared_short(
        self,
        prepared: PreparedShortSequence,
    ) -> Sequence:
        sequence = prepared.state.sequence
        with self.repository.transaction():
            if prepared.new_sequence:
                self.repository.catalog.commit_short_sequence(sequence)
            self.repository.audio.replace_audio_graph(
                sequence.id,
                list(prepared.audio_buses),
                list(prepared.audio_effects),
            )
            self.repository.timeline.save_timeline(prepared.state)
            self.repository.subtitles.add_subtitle_placements(
                list(prepared.subtitle_placements)
            )
        return self.repository.catalog.get_sequence(sequence.id)
