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
from mediaflow.domain.project import ProjectProfile, Sequence
from mediaflow.domain.subtitles import SubtitlePlacement
from mediaflow.domain.timebase import reframe_frames, source_frame_at_timeline_offset
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


@dataclass(frozen=True, slots=True)
class _AudioCopy:
    bus_ids: dict[str, str]
    buses: tuple[AudioBus, ...]
    effects: tuple[AudioEffect, ...]


@dataclass(frozen=True, slots=True)
class _TrackCopy:
    track_ids: dict[str, str]
    tracks: tuple[Track, ...]


@dataclass(frozen=True, slots=True)
class _ClipCopy:
    clip_ids: dict[str, str]
    clips: tuple[Clip, ...]


class SequenceCopyPlanner:
    def __init__(self, documents: SequenceServiceDocuments) -> None:
        self.documents = documents

    def prepare(
        self,
        source: TimelineState,
        selected: TimelineRange,
        *,
        name: str | None,
        destination_sequence: Sequence | None = None,
    ) -> PreparedShortSequence:
        destination, new_sequence, source_profile, destination_profile = self._destination(
            source,
            selected,
            name=name,
            destination_sequence=destination_sequence,
        )
        audio = self._clone_audio(source, destination.sequence.id)
        tracks = self._clone_tracks(
            source,
            destination.sequence.id,
            audio.bus_ids,
        )
        destination.tracks = list(tracks.tracks)
        clips = self._clone_clips(source, selected, tracks.track_ids)
        destination.clips = list(clips.clips)
        self._clone_relationships(
            source,
            destination,
            tracks.track_ids,
            clips.clip_ids,
        )
        self._clone_annotations(source, destination, selected)
        destination = reframe_timeline_clock(
            destination,
            self.documents.assets.list_assets(),
            destination_profile,
            asset_source_profile=project_frame_profile(
                self.documents.projects,
                self.documents.sequences,
            ),
        ).state
        placements = self._clone_subtitle_placements(
            source,
            selected,
            tracks.track_ids,
            clips.clip_ids,
            source_profile,
            destination_profile,
        )
        return PreparedShortSequence(
            state=destination,
            audio_buses=audio.buses,
            audio_effects=audio.effects,
            subtitle_placements=placements,
            new_sequence=new_sequence,
        )

    def _destination(
        self,
        source: TimelineState,
        selected: TimelineRange,
        *,
        name: str | None,
        destination_sequence: Sequence | None,
    ) -> tuple[TimelineState, bool, ProjectProfile, ProjectProfile]:
        new_sequence = destination_sequence is None
        sequence = destination_sequence or self.documents.sequences.prepare_short_sequence(
            name or selected.name or "短视频"
        )
        source_profile = source.sequence.profile
        destination_profile = sequence.profile
        destination_name = (name or selected.name or sequence.name).strip()
        sequence = sequence.model_copy(
            update={
                "name": destination_name or sequence.name,
                "profile": source_profile,
                "in_out": None,
            }
        )
        return (
            TimelineState(sequence=sequence),
            new_sequence,
            source_profile,
            destination_profile,
        )

    def _clone_audio(
        self,
        source: TimelineState,
        destination_sequence_id: str,
    ) -> _AudioCopy:
        source_buses = self.documents.audio.list_audio_buses(source.sequence.id)
        bus_ids = {bus.id: new_id() for bus in source_buses}
        buses = tuple(
            AudioBus(
                id=bus_ids[bus.id],
                sequence_id=destination_sequence_id,
                name=bus.name,
                parent_bus_id=(bus_ids[bus.parent_bus_id] if bus.parent_bus_id is not None else None),
                position=bus.position,
                gain_db=bus.gain_db,
                muted=bus.muted,
                solo=bus.solo,
                channel_layout=bus.channel_layout,
            )
            for bus in source_buses
        )
        effects: list[AudioEffect] = []
        for bus in source_buses:
            for effect in self.documents.audio.list_audio_effects(bus.id):
                parameters = dict(effect.parameters)
                if effect.kind == AudioEffectKind.DUCKING:
                    driver_bus_id = str(parameters.get("driver_bus_id", ""))
                    if driver_bus_id and driver_bus_id not in bus_ids:
                        raise ValueError("源序列的闪避效果引用了不存在的音频总线")
                    if driver_bus_id:
                        parameters["driver_bus_id"] = bus_ids[driver_bus_id]
                effects.append(
                    AudioEffect(
                        id=new_id(),
                        bus_id=bus_ids[bus.id],
                        kind=effect.kind,
                        position=effect.position,
                        enabled=effect.enabled,
                        parameters=parameters,
                    )
                )
        return _AudioCopy(bus_ids, buses, tuple(effects))

    @staticmethod
    def _clone_tracks(
        source: TimelineState,
        destination_sequence_id: str,
        bus_ids: dict[str, str],
    ) -> _TrackCopy:
        source_tracks = sorted(source.tracks, key=lambda item: item.position)
        track_ids: dict[str, str] = {}
        tracks: list[Track] = []
        for position, track in enumerate(source_tracks):
            copied = Track(
                sequence_id=destination_sequence_id,
                name=track.name,
                kind=track.kind,
                position=position,
                enabled=track.enabled,
                locked=False,
                muted=track.muted,
                solo=track.solo,
                audio_bus_id=(bus_ids[track.audio_bus_id] if track.audio_bus_id is not None else None),
                primary_dialogue=track.primary_dialogue,
            )
            tracks.append(copied)
            track_ids[track.id] = copied.id
        tracks = [
            track.model_copy(
                update={"linked_audio_track_id": track_ids.get(source_track.linked_audio_track_id or "")}
            )
            for track, source_track in zip(tracks, source_tracks, strict=True)
        ]
        return _TrackCopy(track_ids, tuple(tracks))

    @staticmethod
    def _clone_clips(
        source: TimelineState,
        selected: TimelineRange,
        track_ids: dict[str, str],
    ) -> _ClipCopy:
        clip_ids: dict[str, str] = {}
        clips: list[Clip] = []
        for clip in source.clips:
            overlap_start = max(selected.start_frame, clip.timeline_start)
            overlap_end = min(selected.end_frame, clip.timeline_end)
            if overlap_end <= overlap_start:
                continue
            timeline_start = overlap_start - selected.start_frame
            copied = Clip(
                id=new_id(),
                track_id=track_ids[clip.track_id],
                asset_id=clip.asset_id,
                timeline_start=timeline_start,
                source_in=source_frame_at_timeline_offset(
                    clip.source_in,
                    overlap_start - clip.timeline_start,
                    clip.speed_numerator,
                    clip.speed_denominator,
                    freeze_source_frame=clip.freeze_source_frame,
                ),
                duration=max(
                    1,
                    overlap_end - selected.start_frame - timeline_start,
                ),
                media_kind=clip.media_kind,
                speed_numerator=clip.speed_numerator,
                speed_denominator=clip.speed_denominator,
                pitch_compensation=clip.pitch_compensation,
                transform=clip.transform,
                transform_keyframes=list(clip.transform_keyframes),
                audio=clip.audio,
            )
            clips.append(copied)
            clip_ids[clip.id] = copied.id
        return _ClipCopy(clip_ids, tuple(clips))

    @staticmethod
    def _clone_relationships(
        source: TimelineState,
        destination: TimelineState,
        track_ids: dict[str, str],
        clip_ids: dict[str, str],
    ) -> None:
        destination.web_states = {
            destination_id: source.web_states[source_id].model_copy(
                update={"clip_id": destination_id, "revision": 0}
            )
            for source_id, destination_id in clip_ids.items()
            if source_id in source.web_states
        }
        destination.compounds = [
            CompoundClip(
                sequence_id=destination.sequence.id,
                name=item.name,
                clip_ids=[clip_ids[clip_id] for clip_id in item.clip_ids],
            )
            for item in source.compounds
            if all(clip_id in clip_ids for clip_id in item.clip_ids)
        ]
        clips_by_id = {clip.id: clip for clip in destination.clips}
        for item in source.transitions:
            if item.left_clip_id not in clip_ids or item.right_clip_id not in clip_ids:
                continue
            left = clips_by_id[clip_ids[item.left_clip_id]]
            right = clips_by_id[clip_ids[item.right_clip_id]]
            if left.timeline_end != right.timeline_start:
                continue
            destination.transitions.append(
                Transition(
                    track_id=track_ids[item.track_id],
                    left_clip_id=left.id,
                    right_clip_id=right.id,
                    kind=item.kind,
                    duration=min(left.duration, right.duration, item.duration),
                    parameters=item.parameters,
                )
            )

    @staticmethod
    def _clone_annotations(
        source: TimelineState,
        destination: TimelineState,
        selected: TimelineRange,
    ) -> None:
        destination.markers = [
            TimelineMarker(
                sequence_id=destination.sequence.id,
                frame=item.frame - selected.start_frame,
                name=item.name,
                color=item.color,
            )
            for item in source.markers
            if selected.start_frame <= item.frame < selected.end_frame
        ]
        for item in source.ranges:
            start = max(item.start_frame, selected.start_frame)
            end = min(item.end_frame, selected.end_frame)
            if end <= start or item.id == selected.id:
                continue
            converted_start = start - selected.start_frame
            destination.ranges.append(
                TimelineRange(
                    sequence_id=destination.sequence.id,
                    start_frame=converted_start,
                    end_frame=max(
                        converted_start + 1,
                        end - selected.start_frame,
                    ),
                    name=item.name,
                    color=item.color,
                )
            )

    def _clone_subtitle_placements(
        self,
        source: TimelineState,
        selected: TimelineRange,
        track_ids: dict[str, str],
        clip_ids: dict[str, str],
        source_profile: ProjectProfile,
        destination_profile: ProjectProfile,
    ) -> tuple[SubtitlePlacement, ...]:
        source_tracks = {track.id: track for track in source.tracks}
        placements: list[SubtitlePlacement] = []
        for source_track_id, destination_track_id in track_ids.items():
            if source_tracks[source_track_id].kind != TrackKind.SUBTITLE:
                continue
            for placement in self.documents.subtitles.list_subtitle_placements(source_track_id):
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
                        clip_id=clip_ids.get(placement.clip_id or ""),
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
        return tuple(placements)
