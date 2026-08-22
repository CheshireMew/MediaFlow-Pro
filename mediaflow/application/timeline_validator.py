from __future__ import annotations

from collections.abc import Mapping

from mediaflow.application.ports import TimelineValidationDocuments
from mediaflow.application.timeline_clock import assets_in_timeline_clock
from mediaflow.application.timeline_integrity import validate_timeline_integrity
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.enums import ClipMediaKind, TrackKind
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import audio_clips_for_track
from mediaflow.domain.timeline import Clip, TimelineState


class TimelineValidator:
    def __init__(self, repository: TimelineValidationDocuments):
        self.repository = repository

    def validate(
        self,
        state: TimelineState,
        *,
        baseline: TimelineState,
        allow_locked_changes: bool = False,
        assets: Mapping[str, Asset] | None = None,
    ) -> None:
        contextual_assets = (
            dict(assets)
            if assets is not None
            else assets_in_timeline_clock(
            self.repository.projects,
            self.repository.sequences,
            self.repository.assets, state.sequence)
        )
        validate_timeline_integrity(state, assets=contextual_assets)
        for track in state.tracks:
            track_clips = state.clips_for_track(track.id)
            if (
                not allow_locked_changes
                and track.locked
                and baseline.clips_for_track(track.id) != track_clips
            ):
                raise PermissionError(f"Track is locked: {track.name}")
        for audio_track in (track for track in state.tracks if track.kind == TrackKind.AUDIO):
            audible_clips = audio_clips_for_track(state, audio_track.id)
            if (
                not allow_locked_changes
                and audio_track.locked
                and audio_clips_for_track(baseline, audio_track.id) != audible_clips
            ):
                raise PermissionError(f"Track is locked: {audio_track.name}")

    def validate_clip_changes(
        self,
        state: TimelineState,
        *,
        baseline: TimelineState,
        clip_ids: set[str],
    ) -> None:
        """Validate an in-place clip delta without rescanning unrelated graph entities."""

        tracks = {track.id: track for track in state.tracks}
        before = {clip.id: clip for clip in baseline.clips if clip.id in clip_ids}
        after = {clip.id: clip for clip in state.clips if clip.id in clip_ids}
        if set(before) != clip_ids or set(after) != clip_ids:
            raise ValueError("Clip delta references a missing clip")

        affected_track_ids = {
            track_id
            for clip_id in clip_ids
            for track_id in (before[clip_id].track_id, after[clip_id].track_id)
        }
        for track_id in affected_track_ids:
            track = tracks.get(track_id)
            if track is None:
                raise ValueError("Clip delta references an unknown track")
            if track.locked:
                raise PermissionError(f"Track is locked: {track.name}")

        assets = assets_in_timeline_clock(
            self.repository.projects,
            self.repository.sequences,
            self.repository.assets,
            state.sequence,
        )
        for clip in after.values():
            track = tracks[clip.track_id]
            asset = assets.get(clip.asset_id)
            if asset is None:
                raise ValueError("Timeline clip references an unknown asset")
            TimelineRules.validate_clip_track(
                asset.kind,
                clip.media_kind,
                track.kind,
                asset.metadata.has_audio,
            )
            clip.validate_source_range(asset.kind, asset.metadata.duration_frames)
            if clip.media_kind == ClipMediaKind.LINKED_AV and track.linked_audio_track_id is None:
                raise ValueError("Linked video audio requires a paired audio track")

        for track_id in affected_track_ids:
            _validate_no_overlap(state.clips_for_track(track_id), tracks[track_id].name, "Clips")

        affected_audio_track_ids = {
            track.id
            for track in tracks.values()
            if track.kind == TrackKind.AUDIO and track.id in affected_track_ids
        }
        affected_audio_track_ids.update(
            linked_audio_track_id
            for clip_id in clip_ids
            for clip in (before[clip_id], after[clip_id])
            if clip.media_kind == ClipMediaKind.LINKED_AV
            and (linked_audio_track_id := tracks[clip.track_id].linked_audio_track_id) is not None
        )
        for track_id in affected_audio_track_ids:
            if tracks[track_id].locked:
                raise PermissionError(f"Track is locked: {tracks[track_id].name}")
            _validate_no_overlap(
                audio_clips_for_track(state, track_id),
                tracks[track_id].name,
                "Audio",
            )


def _validate_no_overlap(clips: list[Clip], track_name: str, subject: str) -> None:
    for left, right in zip(clips, clips[1:], strict=False):
        if right.timeline_start < left.timeline_end:
            raise ValueError(f"{subject} cannot overlap on the same track: {track_name}")
