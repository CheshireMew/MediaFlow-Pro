from __future__ import annotations

from mediaflow.domain.enums import AssetKind, ClipMediaKind, TrackKind
from mediaflow.domain.project import SequenceInOut
from mediaflow.domain.sequence_audio import audio_clips_for_track
from mediaflow.domain.timeline import (
    Clip,
    CompoundClip,
    TimelineState,
    Transition,
    compatible_track_kinds,
)


class TimelineRules:
    @staticmethod
    def normalize_sequence_in_out(state: TimelineState) -> None:
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
    def assign_default_primary_dialogue_track(state: TimelineState) -> None:
        if any(track.primary_dialogue for track in state.tracks):
            return
        candidates: list[tuple[int, int, str, str]] = []
        for audio_track in (track for track in state.tracks if track.kind == TrackKind.AUDIO):
            for clip in audio_clips_for_track(state, audio_track.id):
                candidates.append(
                    (
                        clip.timeline_start,
                        audio_track.position,
                        clip.id,
                        audio_track.id,
                    )
                )
        if not candidates:
            return
        primary_track_id = min(candidates)[3]
        state.tracks = [
            track.model_copy(update={"primary_dialogue": track.id == primary_track_id})
            if track.kind == TrackKind.AUDIO
            else track
            for track in state.tracks
        ]

    @staticmethod
    def normalize_compounds(state: TimelineState) -> None:
        clips = {clip.id: clip for clip in state.clips}
        normalized: list[CompoundClip] = []
        for compound in state.compounds:
            if any(clip_id not in clips for clip_id in compound.clip_ids):
                continue
            members = [clips[clip_id] for clip_id in compound.clip_ids]
            if len({clip.track_id for clip in members}) != 1:
                continue
            ordered = sorted(members, key=lambda clip: (clip.timeline_start, clip.id))
            if any(
                left.timeline_end != right.timeline_start
                for left, right in zip(ordered, ordered[1:], strict=False)
            ):
                continue
            normalized.append(compound.model_copy(update={"clip_ids": [clip.id for clip in ordered]}))
        state.compounds = normalized

    @staticmethod
    def transition_is_valid(transition: Transition, clips: dict[str, Clip]) -> bool:
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
    def validate_clip_track(
        asset_kind: AssetKind,
        media_kind: ClipMediaKind,
        track_kind: TrackKind,
        has_audio: bool,
    ) -> None:
        if track_kind not in compatible_track_kinds(media_kind):
            raise ValueError(f"Cannot place {media_kind.value} on a {track_kind.value} track")
        if media_kind == ClipMediaKind.LINKED_AV and (
            asset_kind not in {AssetKind.VIDEO, AssetKind.WEB} or not has_audio
        ):
            raise ValueError(
                "Only video or editable media assets with audio can create linked clips"
            )
        if media_kind == ClipMediaKind.AUDIO_ONLY and (
            asset_kind not in {AssetKind.VIDEO, AssetKind.AUDIO, AssetKind.WEB}
            or not has_audio
        ):
            raise ValueError("The source asset has no audio component")
        if media_kind == ClipMediaKind.VIDEO_ONLY and asset_kind not in {
            AssetKind.VIDEO,
            AssetKind.IMAGE,
            AssetKind.WEB,
        }:
            raise ValueError("The source asset has no video component")

    @staticmethod
    def renumber_tracks(state: TimelineState) -> None:
        state.tracks = [
            track.model_copy(update={"position": position}) for position, track in enumerate(state.tracks)
        ]

    @staticmethod
    def interval_available(
        state: TimelineState,
        track_id: str,
        start: int,
        duration: int,
    ) -> bool:
        end = start + duration
        return all(
            end <= clip.timeline_start or start >= clip.timeline_end
            for clip in state.clips_for_track(track_id)
        )

    @staticmethod
    def track_label(kind: TrackKind) -> str:
        return {
            TrackKind.VIDEO: "视频",
            TrackKind.AUDIO: "音频",
            TrackKind.SUBTITLE: "字幕",
        }[kind]
