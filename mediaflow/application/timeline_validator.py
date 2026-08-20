from __future__ import annotations

from collections.abc import Mapping

from mediaflow.application.ports import TimelineValidationDocuments
from mediaflow.application.timeline_clock import assets_in_timeline_clock
from mediaflow.application.timeline_integrity import validate_timeline_integrity
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import audio_clips_for_track
from mediaflow.domain.timeline import TimelineState


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
