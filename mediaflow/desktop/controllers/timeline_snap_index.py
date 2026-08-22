from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from typing import Any


class TimelineSnapTargetIndex:
    """Sorted local snap targets shared by release-time timeline edits."""

    def __init__(self) -> None:
        self._state_identity = 0
        self._subtitle_revision = -1
        self._entries: list[tuple[int, str, str]] = []
        self._frames: list[int] = []

    @property
    def target_count(self) -> int:
        return len(self._entries)

    def is_current(self, state: Any, subtitle_revision: int) -> bool:
        return (
            self._state_identity == id(state)
            and self._subtitle_revision == subtitle_revision
        )

    def invalidate(self) -> None:
        self._state_identity = 0

    def rebuild(
        self,
        state: Any,
        subtitle_rows: Iterable[dict[str, Any]],
        subtitle_revision: int,
    ) -> None:
        entries = [
            edge
            for clip in state.clips
            for edge in (
                (clip.timeline_start, "clip", clip.id),
                (clip.timeline_end, "clip", clip.id),
            )
        ]
        entries.extend(
            edge
            for row in subtitle_rows
            for edge in (
                (int(row["startFrame"]), "subtitle", str(row["placementId"])),
                (int(row["endFrame"]), "subtitle", str(row["placementId"])),
            )
        )
        entries.extend((marker.frame, "marker", marker.id) for marker in state.markers)
        entries.extend(
            edge
            for item in state.ranges
            for edge in (
                (item.start_frame, "range", item.id),
                (item.end_frame, "range", item.id),
            )
        )
        entries.sort()
        self._entries = entries
        self._frames = [item[0] for item in entries]
        self._state_identity = id(state)
        self._subtitle_revision = subtitle_revision

    def update_clips(self, state: Any, clips: Iterable[Any]) -> None:
        changed = {clip.id: clip for clip in clips}
        if not changed or not self._state_identity:
            return
        self._entries = [
            entry
            for entry in self._entries
            if entry[1] != "clip" or entry[2] not in changed
        ]
        self._entries.extend(
            edge
            for clip in changed.values()
            for edge in (
                (clip.timeline_start, "clip", clip.id),
                (clip.timeline_end, "clip", clip.id),
            )
        )
        self._entries.sort()
        self._frames = [item[0] for item in self._entries]
        self._state_identity = id(state)

    def snap(
        self,
        frame: int,
        tolerance_frames: int,
        *,
        playhead_frame: int,
        excluded_clip_ids: set[str],
        excluded_subtitle_ids: set[str],
    ) -> int:
        left = bisect_left(self._frames, frame - tolerance_frames)
        right = bisect_right(self._frames, frame + tolerance_frames)
        candidates = [0, max(0, playhead_frame)]
        candidates.extend(
            target
            for target, kind, owner_id in self._entries[left:right]
            if not (
                (kind == "clip" and owner_id in excluded_clip_ids)
                or (kind == "subtitle" and owner_id in excluded_subtitle_ids)
            )
        )
        candidates = [
            target
            for target in candidates
            if abs(target - frame) <= tolerance_frames
        ]
        if not candidates:
            return frame
        return min(candidates, key=lambda target: (abs(target - frame), target))
