from __future__ import annotations

from mediaflow.domain.enums import TrackKind
from mediaflow.domain.models import (
    Clip,
    SubtitlePlacement,
    TimelineMarker,
    TimelineRange,
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


class SequenceService:
    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    def create_short_from_range(
        self,
        source_sequence_id: str,
        range_id: str,
        *,
        name: str | None = None,
    ):
        source = self.repository.load_timeline(source_sequence_id)
        try:
            selected = next(item for item in source.ranges if item.id == range_id)
        except StopIteration as error:
            raise KeyError(range_id) from error
        destination_sequence = self.repository.create_short_sequence(name or selected.name or "短视频")
        destination = self.repository.load_timeline(destination_sequence.id)
        source_profile = source.sequence.profile
        destination_profile = destination.sequence.profile

        source_buses = {item.id: item for item in self.repository.list_audio_buses(source_sequence_id)}
        destination_buses = self.repository.list_audio_buses(destination_sequence.id)
        destination_bus_by_name = {item.name: item.id for item in destination_buses}
        master_bus_id = next(item.id for item in destination_buses if item.parent_bus_id is None)

        track_map: dict[str, str] = {}
        destination.tracks = []
        for position, track in enumerate(sorted(source.tracks, key=lambda item: item.position)):
            audio_bus_id = None
            if track.audio_bus_id:
                source_bus = source_buses.get(track.audio_bus_id)
                audio_bus_id = destination_bus_by_name.get(
                    source_bus.name if source_bus else "",
                    master_bus_id,
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
            )
            destination.tracks.append(copied_track)
            track_map[track.id] = copied_track.id

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
                clip.source_in + source_delta
                if clip.speed_numerator > 0
                else clip.source_in - source_delta
            )
            timeline_start = self._convert(
                overlap_start - selected.start_frame,
                source_profile,
                destination_profile,
            )
            timeline_end = self._convert(
                overlap_end - selected.start_frame,
                source_profile,
                destination_profile,
            )
            copied = Clip(
                id=new_id(),
                track_id=track_map[clip.track_id],
                asset_id=clip.asset_id,
                timeline_start=timeline_start,
                source_in=self._convert(source_in, source_profile, destination_profile),
                duration=max(1, timeline_end - timeline_start),
                speed_numerator=clip.speed_numerator,
                speed_denominator=clip.speed_denominator,
                pitch_compensation=clip.pitch_compensation,
                transform=clip.transform,
                audio=clip.audio,
            )
            destination.clips.append(copied)
            clip_map[clip.id] = copied.id

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
                        max(1, self._convert(item.duration, source_profile, destination_profile)),
                    ),
                    parameters=item.parameters,
                )
            )

        destination.markers = [
            TimelineMarker(
                sequence_id=destination_sequence.id,
                frame=self._convert(
                    item.frame - selected.start_frame,
                    source_profile,
                    destination_profile,
                ),
                name=item.name,
                color=item.color,
            )
            for item in source.markers
            if selected.start_frame <= item.frame < selected.end_frame
        ]
        destination.ranges = []
        for item in source.ranges:
            start = max(item.start_frame, selected.start_frame)
            end = min(item.end_frame, selected.end_frame)
            if end <= start or item.id == selected.id:
                continue
            converted_start = self._convert(
                start - selected.start_frame,
                source_profile,
                destination_profile,
            )
            destination.ranges.append(
                TimelineRange(
                    sequence_id=destination_sequence.id,
                    start_frame=converted_start,
                    end_frame=max(
                        converted_start + 1,
                        self._convert(
                            end - selected.start_frame,
                            source_profile,
                            destination_profile,
                        ),
                    ),
                    name=item.name,
                    color=item.color,
                )
            )
        self.repository.save_timeline(destination)

        placements: list[SubtitlePlacement] = []
        for source_track_id, destination_track_id in track_map.items():
            source_track = next(item for item in source.tracks if item.id == source_track_id)
            if source_track.kind != TrackKind.SUBTITLE:
                continue
            for placement in self.repository.list_subtitle_placements(source_track_id):
                start = max(placement.start_frame, selected.start_frame)
                end = min(placement.end_frame, selected.end_frame)
                if end <= start:
                    continue
                converted_start = self._convert(
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
                            self._convert(
                                end - selected.start_frame,
                                source_profile,
                                destination_profile,
                            ),
                        ),
                        text_override=placement.text_override,
                    )
                )
        self.repository.add_subtitle_placements(placements)
        return self.repository.get_sequence(destination_sequence.id)

    @staticmethod
    def _convert(value: int, source_profile, destination_profile) -> int:
        return seconds_to_frames(
            frames_to_seconds(
                value,
                source_profile.fps_numerator,
                source_profile.fps_denominator,
            ),
            destination_profile.fps_numerator,
            destination_profile.fps_denominator,
        )
