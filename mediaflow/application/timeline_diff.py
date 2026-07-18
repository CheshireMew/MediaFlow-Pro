from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from mediaflow.domain.timeline import Clip, TimelineRange, TimelineState


@dataclass(frozen=True, slots=True, order=True)
class FrameInterval:
    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("Frame interval must have a positive length")

    @property
    def duration(self) -> int:
        return self.end_frame - self.start_frame


@dataclass(frozen=True, slots=True)
class RippleAdjustment:
    interval: FrameInterval
    delta_frames: int

    def __post_init__(self) -> None:
        if self.delta_frames != -self.interval.duration:
            raise ValueError("Ripple adjustment must close its complete vacated interval")


class TimelineDiff:
    """Derive reusable timeline adjustments from two canonical timeline snapshots."""

    @classmethod
    def ripple_adjustments(
        cls,
        before: TimelineState,
        after: TimelineState,
        *,
        source_track_ids: set[str],
    ) -> list[RippleAdjustment]:
        if before.sequence.id != after.sequence.id:
            raise ValueError("Timeline snapshots must belong to the same sequence")
        vacated: list[FrameInterval] = []
        for track_id in source_track_ids:
            before_intervals = cls._occupied_intervals(before.clips, {track_id})
            after_intervals = cls._occupied_intervals(after.clips, {track_id})
            vacated.extend(cls._subtract_intervals(before_intervals, after_intervals))
        vacated = cls._merge_intervals(vacated)
        return [RippleAdjustment(interval=item, delta_frames=-item.duration) for item in vacated]

    @classmethod
    def apply_ripple(
        cls,
        before: TimelineState,
        after: TimelineState,
        *,
        source_track_ids: set[str],
    ) -> list[RippleAdjustment]:
        adjustments = cls.ripple_adjustments(
            before,
            after,
            source_track_ids=source_track_ids,
        )
        if not adjustments:
            return []
        intervals = [item.interval for item in adjustments]
        locked_track_ids = {track.id for track in after.tracks if track.locked}
        after.clips = [
            clip
            if clip.track_id in locked_track_ids
            else clip.model_copy(
                update={"timeline_start": cls._shift_clip_start(clip.timeline_start, intervals)}
            )
            for clip in after.clips
        ]
        after.markers = [
            marker.model_copy(update={"frame": cls._collapse_frame(marker.frame, intervals)})
            for marker in after.markers
        ]
        adjusted_ranges: list[TimelineRange] = []
        for item in after.ranges:
            start_frame = cls._collapse_frame(item.start_frame, intervals)
            end_frame = cls._collapse_frame(item.end_frame, intervals)
            if end_frame > start_frame:
                adjusted_ranges.append(
                    item.model_copy(update={"start_frame": start_frame, "end_frame": end_frame})
                )
        after.ranges = adjusted_ranges
        return adjustments

    @staticmethod
    def _shift_clip_start(frame: int, intervals: list[FrameInterval]) -> int:
        shift = sum(item.duration for item in intervals if frame >= item.end_frame)
        return frame - shift

    @staticmethod
    def _collapse_frame(frame: int, intervals: list[FrameInterval]) -> int:
        shift = 0
        for item in intervals:
            if frame < item.start_frame:
                break
            if frame < item.end_frame:
                return item.start_frame - shift
            shift += item.duration
        return frame - shift

    @classmethod
    def _occupied_intervals(
        cls,
        clips: Iterable[Clip],
        track_ids: set[str],
    ) -> list[FrameInterval]:
        return cls._merge_intervals(
            FrameInterval(clip.timeline_start, clip.timeline_end)
            for clip in clips
            if clip.track_id in track_ids
        )

    @staticmethod
    def _subtract_intervals(
        sources: list[FrameInterval],
        occupied: list[FrameInterval],
    ) -> list[FrameInterval]:
        result: list[FrameInterval] = []
        for source in sources:
            cursor = source.start_frame
            for blocker in occupied:
                if blocker.end_frame <= cursor:
                    continue
                if blocker.start_frame >= source.end_frame:
                    break
                if blocker.start_frame > cursor:
                    result.append(FrameInterval(cursor, min(blocker.start_frame, source.end_frame)))
                cursor = max(cursor, blocker.end_frame)
                if cursor >= source.end_frame:
                    break
            if cursor < source.end_frame:
                result.append(FrameInterval(cursor, source.end_frame))
        return TimelineDiff._merge_intervals(result)

    @staticmethod
    def _merge_intervals(intervals: Iterable[FrameInterval]) -> list[FrameInterval]:
        merged: list[FrameInterval] = []
        for item in sorted(intervals):
            if not merged or item.start_frame > merged[-1].end_frame:
                merged.append(item)
                continue
            previous = merged[-1]
            merged[-1] = FrameInterval(
                previous.start_frame,
                max(previous.end_frame, item.end_frame),
            )
        return merged
