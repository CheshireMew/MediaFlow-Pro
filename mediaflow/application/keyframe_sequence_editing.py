from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from typing import Any, Protocol, Self


class TimelineKeyframe(Protocol):
    @property
    def timeline_offset(self) -> int | None: ...

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self: ...


def move_timeline_keyframe[KeyframeT: TimelineKeyframe](
    keyframes: Sequence[KeyframeT],
    old_offset: int,
    new_offset: int,
    *,
    validate_offset: Callable[[int], None],
    occupied_message: str,
) -> list[KeyframeT]:
    validate_offset(new_offset)
    moving = next((item for item in keyframes if item.timeline_offset == old_offset), None)
    if moving is None:
        raise KeyError(old_offset)
    if old_offset != new_offset and any(item.timeline_offset == new_offset for item in keyframes):
        raise ValueError(occupied_message)
    return sorted(
        [
            item.model_copy(update={"timeline_offset": new_offset})
            if item.timeline_offset == old_offset
            else item
            for item in keyframes
        ],
        key=lambda item: int(item.timeline_offset or 0),
    )


def retime_timeline_keyframes[KeyframeT: TimelineKeyframe](
    keyframes: Sequence[KeyframeT],
    timeline_offsets: Sequence[int],
    *,
    anchor_offset: int,
    scale: float,
    validate_offset: Callable[[int], None],
    empty_message: str,
    occupied_message: str,
) -> list[KeyframeT]:
    if scale <= 0:
        raise ValueError("关键帧时间缩放必须大于零")
    selected = set(timeline_offsets)
    if not selected:
        raise ValueError(empty_message)
    missing = selected - {int(item.timeline_offset or 0) for item in keyframes}
    if missing:
        raise KeyError(min(missing))
    retimed = []
    for item in keyframes:
        offset = int(item.timeline_offset or 0)
        if offset in selected:
            offset = round(anchor_offset + (offset - anchor_offset) * scale)
            validate_offset(offset)
            item = item.model_copy(update={"timeline_offset": offset})
        retimed.append(item)
    offsets = [int(item.timeline_offset or 0) for item in retimed]
    if len(offsets) != len(set(offsets)):
        raise ValueError(occupied_message)
    return sorted(retimed, key=lambda item: int(item.timeline_offset or 0))


def paste_timeline_keyframes[KeyframeT: TimelineKeyframe](
    existing: Sequence[KeyframeT],
    source: Sequence[KeyframeT],
    *,
    destination_offset: int,
    scale: float,
    validate_offset: Callable[[int], None],
    prepare: Callable[[KeyframeT, int], KeyframeT],
) -> list[KeyframeT]:
    """Align a copied selection, with pasted points replacing points at the same frame."""

    if not isfinite(scale) or scale <= 0:
        raise ValueError("关键帧时间缩放必须大于零")
    if not source:
        raise ValueError("请至少选择一个要粘贴的关键帧")
    if any(item.timeline_offset is None for item in source):
        raise ValueError("粘贴的关键帧必须使用片段内时间线帧")
    source_start = min(int(item.timeline_offset or 0) for item in source)
    replacements: dict[int, KeyframeT] = {}
    for item in source:
        offset = destination_offset + round((int(item.timeline_offset or 0) - source_start) * scale)
        validate_offset(offset)
        replacements[offset] = prepare(item, offset)
    merged = [item for item in existing if item.timeline_offset not in replacements]
    merged.extend(replacements.values())
    return sorted(merged, key=lambda item: int(item.timeline_offset or 0))
