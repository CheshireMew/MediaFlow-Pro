from __future__ import annotations

from collections.abc import Mapping

from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.enums import AssetKind, ClipMediaKind, TrackKind
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import audio_clips_for_track
from mediaflow.domain.timeline import Clip, TimelineState


def validate_timeline_integrity(
    state: TimelineState,
    *,
    assets: Mapping[str, Asset],
) -> None:
    """Validate the persisted timeline graph at its application boundary.

    Editing and repository writes both consume this function so a timeline
    cannot be accepted by one boundary and rejected by the other.
    """

    tracks = {track.id: track for track in state.tracks}
    if len(tracks) != len(state.tracks):
        raise ValueError("Track identifiers must be unique")
    if any(track.sequence_id != state.sequence.id for track in state.tracks):
        raise ValueError("Timeline contains a track from another sequence")
    if len({track.position for track in state.tracks}) != len(state.tracks):
        raise ValueError("Track positions must be unique")
    if [track.position for track in state.tracks] != list(range(len(state.tracks))):
        raise ValueError("Track positions must match timeline order")

    linked_audio_track_ids = [
        track.linked_audio_track_id for track in state.tracks if track.linked_audio_track_id is not None
    ]
    if len(set(linked_audio_track_ids)) != len(linked_audio_track_ids):
        raise ValueError("An audio track can only be paired with one video track")
    primary_dialogue_tracks = [track for track in state.tracks if track.primary_dialogue]
    if len(primary_dialogue_tracks) > 1:
        raise ValueError("A sequence can only have one primary dialogue track")
    if any(track.kind != TrackKind.AUDIO for track in primary_dialogue_tracks):
        raise ValueError("The primary dialogue track must be an audio track")
    for track in state.tracks:
        if track.linked_audio_track_id is None:
            continue
        linked = tracks.get(track.linked_audio_track_id)
        if track.kind != TrackKind.VIDEO or linked is None or linked.kind != TrackKind.AUDIO:
            raise ValueError("Video tracks can only link to an audio track in the same sequence")

    clips_by_id = {clip.id: clip for clip in state.clips}
    if len(clips_by_id) != len(state.clips):
        raise ValueError("Clip identifiers must be unique")
    if any(clip.track_id not in tracks for clip in state.clips):
        raise ValueError("Clip references an unknown track")
    unknown_asset_ids = {clip.asset_id for clip in state.clips}.difference(assets)
    if unknown_asset_ids:
        raise ValueError("Timeline clip references an unknown asset")

    for track in state.tracks:
        previous: Clip | None = None
        for clip in state.clips_for_track(track.id):
            asset = assets[clip.asset_id]
            TimelineRules.validate_clip_track(
                asset.kind,
                clip.media_kind,
                track.kind,
                asset.metadata.has_audio,
            )
            clip.validate_source_range(asset.kind, asset.metadata.duration_frames)
            if clip.media_kind == ClipMediaKind.LINKED_AV and track.linked_audio_track_id is None:
                raise ValueError("Linked video audio requires a paired audio track")
            if previous is not None and clip.timeline_start < previous.timeline_end:
                raise ValueError(f"Clips cannot overlap on the same track: {track.name}")
            previous = clip

    for audio_track in (track for track in state.tracks if track.kind == TrackKind.AUDIO):
        audible_clips = audio_clips_for_track(state, audio_track.id)
        for left, right in zip(audible_clips, audible_clips[1:], strict=False):
            if right.timeline_start < left.timeline_end:
                raise ValueError(f"Audio cannot overlap on the same track: {audio_track.name}")

    if any(marker.sequence_id != state.sequence.id for marker in state.markers):
        raise ValueError("Marker references another sequence")
    if any(item.sequence_id != state.sequence.id for item in state.ranges):
        raise ValueError("Range references another sequence")
    if len({marker.id for marker in state.markers}) != len(state.markers):
        raise ValueError("Marker identifiers must be unique")
    if len({item.id for item in state.ranges}) != len(state.ranges):
        raise ValueError("Range identifiers must be unique")

    compound_members: set[str] = set()
    for compound in state.compounds:
        if compound.sequence_id != state.sequence.id:
            raise ValueError("Compound clip references another sequence")
        if any(clip_id not in clips_by_id for clip_id in compound.clip_ids):
            raise ValueError("Compound clip references an unknown clip")
        if compound_members.intersection(compound.clip_ids):
            raise ValueError("A clip cannot belong to more than one compound clip")
        compound_members.update(compound.clip_ids)
        members = [clips_by_id[clip_id] for clip_id in compound.clip_ids]
        if len({clip.track_id for clip in members}) != 1:
            raise ValueError("Compound clip members must be on one track")
        ordered = sorted(members, key=lambda clip: (clip.timeline_start, clip.id))
        if [clip.id for clip in ordered] != compound.clip_ids:
            raise ValueError("Compound clip members must be stored in timeline order")
        if any(
            left.timeline_end != right.timeline_start
            for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError("Compound clip members must remain adjacent")

    for transition in state.transitions:
        if not TimelineRules.transition_is_valid(transition, clips_by_id):
            raise ValueError("Transition references clips that are no longer adjacent")

    web_clip_ids = {clip.id for clip in state.clips if assets[clip.asset_id].kind == AssetKind.WEB}
    if set(state.web_states) != web_clip_ids:
        raise ValueError("Every web clip must have exactly one editable media state")
    if any(web_state.clip_id != clip_id for clip_id, web_state in state.web_states.items()):
        raise ValueError("Editable media state key must match its clip identifier")
