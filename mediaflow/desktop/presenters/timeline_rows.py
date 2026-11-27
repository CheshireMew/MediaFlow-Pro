from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QUrl

from mediaflow.domain.enums import ClipMediaKind
from mediaflow.domain.subtitles import SubtitlePlacement, SubtitleSegment
from mediaflow.domain.timeline import Clip, TimelineState


def audio_lane_projection(state: TimelineState) -> dict[str, int]:
    ordered_tracks = sorted(state.tracks, key=lambda track: (track.position, track.id))
    positions = {track.id: index for index, track in enumerate(ordered_tracks)}
    tracks = {track.id: track for track in ordered_tracks}
    result: dict[str, int] = {}
    for clip in state.clips:
        linked = tracks[clip.track_id].linked_audio_track_id
        if clip.media_kind == ClipMediaKind.LINKED_AV and linked is not None and linked in positions:
            result[clip.id] = positions[linked]
    return result


def project_clip_row(
    clip: Clip,
    *,
    asset_fields: Mapping[str, Any],
    track_kind: str,
    track_position: int,
    audio_track_position: int,
    filmstrip_frames: Sequence[dict[str, Any]],
    compound_id: str = "",
) -> dict[str, Any]:
    """Build the same row for full, incremental, and membership projections."""

    return {
        **asset_fields,
        "clipId": clip.id,
        "assetId": clip.asset_id,
        "trackId": clip.track_id,
        "trackKind": track_kind,
        "trackPosition": track_position,
        "sourceIn": clip.source_in,
        "startFrame": clip.timeline_start,
        "durationFrames": clip.duration,
        "endFrame": clip.timeline_end,
        "speed": clip.speed_numerator / clip.speed_denominator,
        "pitchCompensation": clip.pitch_compensation,
        "mediaKind": clip.media_kind.value,
        "audioTrackPosition": audio_track_position,
        "filmstripFrames": [
            {**item, "url": QUrl.fromLocalFile(str(item["path"])).toString()} for item in filmstrip_frames
        ],
        "x": clip.transform.x,
        "y": clip.transform.y,
        "scaleX": clip.transform.scale_x,
        "scaleY": clip.transform.scale_y,
        "rotation": clip.transform.rotation,
        "cropLeft": clip.transform.crop_left,
        "cropTop": clip.transform.crop_top,
        "cropRight": clip.transform.crop_right,
        "cropBottom": clip.transform.crop_bottom,
        "opacity": clip.transform.opacity,
        "gainDb": clip.audio.gain_db,
        "pan": clip.audio.pan,
        "fadeInFrames": clip.audio.fade_in_frames,
        "fadeOutFrames": clip.audio.fade_out_frames,
        "compoundId": compound_id,
        "canDetachAudio": clip.media_kind == ClipMediaKind.LINKED_AV,
        "transformKeyframeCount": len(clip.transform_keyframes),
        "transformKeyframeSource": clip.transform_keyframes[0].source if clip.transform_keyframes else "",
    }


def project_subtitle_placement_row(
    placement: SubtitlePlacement,
    segment: SubtitleSegment,
    *,
    audio_lane_positions: Mapping[str, int],
    default_audio_position: int,
) -> dict[str, Any]:
    return {
        "placementId": placement.id,
        "trackId": placement.track_id,
        "documentId": segment.document_id,
        "segmentId": placement.segment_id,
        "clipId": placement.clip_id or "",
        "audioTrackPosition": audio_lane_positions.get(placement.clip_id or "", default_audio_position),
        "startFrame": placement.start_frame,
        "endFrame": placement.end_frame,
        "text": placement.text_override or segment.text,
        "sourceText": segment.text,
        "hasOverride": placement.text_override is not None,
        "timingOverridden": placement.timing_overridden,
    }
