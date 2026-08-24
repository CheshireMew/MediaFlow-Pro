from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Protocol

from mediaflow.domain.timebase import round_fraction

PlacementKey = tuple[str, str | None]
PlacementRange = tuple[int, int]

class RowValues(Protocol):
    def __getitem__(self, key: str) -> Any: ...


ConvertedSegment = tuple[RowValues, int, int]


@dataclass(frozen=True, slots=True)
class PlacementUpdate:
    placement_id: str
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True)
class PlacementInsert:
    segment_id: str
    clip_id: str | None
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True)
class PlacementReconciliation:
    stale_ids: tuple[str, ...]
    updates: tuple[PlacementUpdate, ...]
    inserts: tuple[PlacementInsert, ...]


def clip_source_range(clip: RowValues) -> tuple[Fraction, Fraction]:
    speed = Fraction(abs(clip["speed_numerator"]), clip["speed_denominator"])
    source_in = Fraction(clip["source_in"])
    consumed = Fraction(clip["duration"]) * speed
    if clip["speed_numerator"] > 0:
        return source_in, source_in + consumed
    return source_in - consumed, source_in


def follow_clip_placements(
    converted_segments: Sequence[ConvertedSegment],
    clips: Iterable[RowValues],
) -> dict[PlacementKey, PlacementRange]:
    starts = [item[1] for item in converted_segments]
    maximum_end = -1
    prefix_maximum_ends: list[int] = []
    for _, _, source_end in converted_segments:
        maximum_end = max(maximum_end, source_end)
        prefix_maximum_ends.append(maximum_end)

    desired: dict[PlacementKey, PlacementRange] = {}
    for clip in clips:
        clip_start, clip_end = clip_source_range(clip)
        first = bisect_right(prefix_maximum_ends, clip_start)
        last = bisect_left(starts, clip_end)
        for segment, source_start, source_end in converted_segments[first:last]:
            mapped = _map_segment_to_clip(source_start, source_end, clip)
            if mapped is not None:
                desired[(str(segment["id"]), str(clip["id"]))] = mapped
    return desired


def offset_placements(
    converted_segments: Iterable[ConvertedSegment],
    *,
    source_start_frame: int | None,
    source_end_frame: int | None,
    offset_frames: int,
) -> dict[PlacementKey, PlacementRange]:
    desired: dict[PlacementKey, PlacementRange] = {}
    for segment, source_start, source_end in converted_segments:
        start = source_start
        end = source_end
        if source_start_frame is not None:
            if end <= source_start_frame:
                continue
            start = max(start, source_start_frame)
        if source_end_frame is not None:
            if start >= source_end_frame:
                continue
            end = min(end, source_end_frame)
        start += offset_frames
        end += offset_frames
        if end > 0:
            desired[(str(segment["id"]), None)] = (max(0, start), max(1, end))
    return desired


def reconcile_placements(
    existing_rows: Iterable[RowValues],
    desired: Mapping[PlacementKey, PlacementRange],
) -> PlacementReconciliation:
    existing: dict[PlacementKey, RowValues] = {}
    duplicate_ids: list[str] = []
    for row in existing_rows:
        key = (str(row["segment_id"]), row["clip_id"])
        if key in existing:
            duplicate_ids.append(str(row["id"]))
        else:
            existing[key] = row
    stale_ids = duplicate_ids + [
        str(row["id"])
        for key, row in existing.items()
        if key not in desired
    ]
    updates: list[PlacementUpdate] = []
    inserts: list[PlacementInsert] = []
    for key, (start, end) in desired.items():
        existing_row = existing.get(key)
        if existing_row is None:
            inserts.append(
                PlacementInsert(
                    segment_id=key[0],
                    clip_id=key[1],
                    start_frame=start,
                    end_frame=end,
                )
            )
        elif not bool(existing_row["timing_overridden"]) and (
            existing_row["start_frame"] != start or existing_row["end_frame"] != end
        ):
            updates.append(
                PlacementUpdate(
                    placement_id=str(existing_row["id"]),
                    start_frame=start,
                    end_frame=end,
                )
            )
    return PlacementReconciliation(
        stale_ids=tuple(stale_ids),
        updates=tuple(updates),
        inserts=tuple(inserts),
    )


def _map_segment_to_clip(
    segment_start: int,
    segment_end: int,
    clip: RowValues,
) -> PlacementRange | None:
    speed = Fraction(abs(clip["speed_numerator"]), clip["speed_denominator"])
    source_in = Fraction(clip["source_in"])
    consumed = Fraction(clip["duration"]) * speed
    if clip["speed_numerator"] > 0:
        start = max(Fraction(segment_start), source_in)
        end = min(Fraction(segment_end), source_in + consumed)
        if end <= start:
            return None
        timeline_start = clip["timeline_start"] + round_fraction((start - source_in) / speed)
        timeline_end = clip["timeline_start"] + round_fraction((end - source_in) / speed)
    else:
        start = max(Fraction(segment_start), source_in - consumed)
        end = min(Fraction(segment_end), source_in)
        if end <= start:
            return None
        timeline_start = clip["timeline_start"] + round_fraction((source_in - end) / speed)
        timeline_end = clip["timeline_start"] + round_fraction((source_in - start) / speed)
    timeline_start = max(
        clip["timeline_start"],
        min(clip["timeline_start"] + clip["duration"] - 1, timeline_start),
    )
    timeline_end = max(
        timeline_start + 1,
        min(clip["timeline_start"] + clip["duration"], timeline_end),
    )
    return timeline_start, timeline_end
