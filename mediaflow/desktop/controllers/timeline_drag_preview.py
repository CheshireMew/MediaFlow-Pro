from __future__ import annotations

from bisect import bisect_left
from typing import Any

from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.enums import AssetKind, ClipMediaKind, TrackKind


class TimelineDragPreviewIndex:
    """Validate high-frequency clip drags against one local timeline snapshot."""

    def __init__(self) -> None:
        self._state_identity = 0
        self._intervals: dict[str, list[tuple[int, int, str]]] = {}
        self._interval_starts: dict[str, list[int]] = {}
        self._clips_by_id: dict[str, Any] = {}

    def invalidate(self) -> None:
        self._state_identity = 0

    def _ensure(self, state: Any) -> None:
        identity = id(state)
        if identity == self._state_identity:
            return
        intervals: dict[str, list[tuple[int, int, str]]] = {}
        for clip in state.clips:
            intervals.setdefault(clip.track_id, []).append(
                (clip.timeline_start, clip.timeline_end, clip.id)
            )
        for rows in intervals.values():
            rows.sort(key=lambda item: (item[0], item[1], item[2]))
        self._intervals = intervals
        self._clips_by_id = {clip.id: clip for clip in state.clips}
        self._interval_starts = {
            track_id: [item[0] for item in rows]
            for track_id, rows in intervals.items()
        }
        self._state_identity = identity

    def preview(
        self,
        *,
        state: Any,
        clips_model: Any,
        selected_ids: list[str],
        clip_id: str,
        start_frame: int,
        requested_track_position: int,
        from_linked_audio: bool,
    ) -> dict[str, object]:
        tracks = sorted(state.tracks, key=lambda item: item.position)
        if not 0 <= requested_track_position < len(tracks):
            return {"accepted": False}
        requested = tracks[requested_track_position]
        if from_linked_audio:
            requested = next(
                (
                    track
                    for track in tracks
                    if track.kind == TrackKind.VIDEO
                    and track.linked_audio_track_id == requested.id
                ),
                None,
            )
            if requested is None:
                return {"accepted": False}
        try:
            self._ensure(state)
            clips_by_id = {
                selected_id: self._clips_by_id[selected_id]
                for selected_id in selected_ids
            }
            primary = clips_by_id[clip_id]
            positions = {track.id: position for position, track in enumerate(tracks)}
            frame_delta = max(0, start_frame) - primary.timeline_start
            track_delta = positions[requested.id] - positions[primary.track_id]
            selected_set = set(selected_ids)
            for selected_id in selected_ids:
                clip = clips_by_id[selected_id]
                source_track = tracks[positions[clip.track_id]]
                destination_position = positions[clip.track_id] + track_delta
                if not 0 <= destination_position < len(tracks):
                    return {"accepted": False}
                destination = tracks[destination_position]
                if source_track.locked or destination.locked:
                    return {"accepted": False}
                row = clips_model.get(clips_model.findRow("clipId", selected_id))
                TimelineRules.validate_clip_track(
                    AssetKind(str(row["assetKind"])),
                    ClipMediaKind(str(row["mediaKind"])),
                    destination.kind,
                    bool(row["hasAudio"]),
                )
                next_start = clip.timeline_start + frame_delta
                next_end = next_start + clip.duration
                if next_start < 0:
                    return {"accepted": False}
                intervals = self._intervals.get(destination.id, [])
                starts = self._interval_starts.get(destination.id, [])
                cursor = bisect_left(starts, next_end) - 1
                while cursor >= 0 and intervals[cursor][1] > next_start:
                    if intervals[cursor][2] not in selected_set:
                        return {"accepted": False}
                    cursor -= 1
        except (KeyError, PermissionError, ValueError):
            return {"accepted": False}
        audio_position = (
            positions.get(requested.linked_audio_track_id, -1)
            if requested.kind == TrackKind.VIDEO
            else -1
        )
        return {
            "accepted": True,
            "trackId": requested.id,
            "trackPosition": positions[requested.id],
            "audioTrackPosition": audio_position,
            "trackKind": requested.kind.value,
        }
