from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from mediaflow.domain.timeline import Clip, TimelineMarker, TimelineRange, TimelineState, Track, Transition


class _Identified(Protocol):
    @property
    def id(self) -> str: ...


def _by_id[EntityT: _Identified](items: Iterable[EntityT], entity_id: str) -> EntityT:
    try:
        return next(item for item in items if item.id == entity_id)
    except StopIteration as error:
        raise KeyError(entity_id) from error


def clip_by_id(state: TimelineState, clip_id: str) -> Clip:
    return _by_id(state.clips, clip_id)


def track_by_id(state: TimelineState, track_id: str) -> Track:
    return _by_id(state.tracks, track_id)


def transition_by_id(state: TimelineState, transition_id: str) -> Transition:
    return _by_id(state.transitions, transition_id)


def marker_by_id(state: TimelineState, marker_id: str) -> TimelineMarker:
    return _by_id(state.markers, marker_id)


def range_by_id(state: TimelineState, range_id: str) -> TimelineRange:
    return _by_id(state.ranges, range_id)


def clip_index(state: TimelineState, clip_id: str) -> int:
    try:
        return next(index for index, clip in enumerate(state.clips) if clip.id == clip_id)
    except StopIteration as error:
        raise KeyError(clip_id) from error
