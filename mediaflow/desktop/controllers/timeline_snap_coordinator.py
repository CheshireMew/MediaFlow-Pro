from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mediaflow.domain.enums import TrackKind

from .timeline_snap_index import TimelineSnapTargetIndex


class TimelineSnappingCoordinator:
    """Own timeline snap indexing and the legacy full-target projection."""

    def __init__(self, session: Any) -> None:
        self._session = session
        self._index = TimelineSnapTargetIndex()

    @property
    def target_count(self) -> int:
        return self._index.target_count

    def targets(
        self,
        excluded_clip_ids: Iterable[str],
        playhead_frame: int,
        *,
        excluded_subtitle_placement_ids: Iterable[str] = (),
    ) -> list[int]:
        targets = [0, max(0, playhead_frame)]
        if not self._session.state.binding.timeline:
            return targets
        excluded = set(excluded_clip_ids)
        excluded_placements = set(excluded_subtitle_placement_ids)
        state = self._session.state.binding.require_timeline().state
        for clip in state.clips:
            if clip.id not in excluded:
                targets.extend([clip.timeline_start, clip.timeline_end])
        subtitle_track_ids = {
            track.id for track in state.tracks if track.kind == TrackKind.SUBTITLE
        }
        for placement in self._session.models.subtitle_placements.snapshot():
            if (
                placement["trackId"] in subtitle_track_ids
                and placement["placementId"] not in excluded_placements
            ):
                targets.extend((int(placement["startFrame"]), int(placement["endFrame"])))
        targets.extend(marker.frame for marker in state.markers)
        for item in state.ranges:
            targets.extend([item.start_frame, item.end_frame])
        return targets

    def snap(
        self,
        frame: int,
        tolerance_frames: int,
        excluded_clip_ids: Iterable[str],
        playhead_frame: int,
        *,
        excluded_subtitle_placement_ids: Iterable[str] = (),
    ) -> int:
        if not self._session.state.binding.timeline:
            return max(0, frame)
        state = self._session.state.binding.require_timeline().state
        revision = self._session.models.subtitle_placements.revision
        if not self._index.is_current(state, revision):
            self._index.rebuild(
                state,
                self._session.models.subtitle_placements.snapshot(),
                revision,
            )
        return self._index.snap(
            max(0, frame),
            max(0, tolerance_frames),
            playhead_frame=playhead_frame,
            excluded_clip_ids=set(excluded_clip_ids),
            excluded_subtitle_ids=set(excluded_subtitle_placement_ids),
        )

    def rebuild(self) -> None:
        if not self._session.state.binding.timeline:
            self._index.invalidate()
            return
        self._index.rebuild(
            self._session.state.binding.require_timeline().state,
            self._session.models.subtitle_placements.snapshot(),
            self._session.models.subtitle_placements.revision,
        )

    def invalidate(self) -> None:
        self._index.invalidate()

    def update_clips(
        self,
        clips: Iterable[object],
        removed_clip_ids: Iterable[str] = (),
        *,
        state: object | None = None,
    ) -> None:
        if self._session.state.binding.timeline:
            self._index.update_clips(
                state or self._session.state.binding.require_timeline().state,
                clips,
                removed_clip_ids,
            )
