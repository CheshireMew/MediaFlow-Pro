from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

from mediaflow.domain.timeline import TimelineState

TIMELINE_HISTORY_MODE = "patch"


class _Identified(Protocol):
    id: str


_Entity = TypeVar("_Entity", bound=_Identified)


def compact_timeline_change(
    source: TimelineState,
    destination: TimelineState,
) -> tuple[TimelineState, TimelineState]:
    """Keep only timeline entities changed by one reversible edit.

    The sequence document remains on both sides because ``TimelineState`` owns
    it as a required field. Every list and web-state map contains only the
    identities whose value or presence changed. ``TimelineMergePolicy`` can
    therefore apply the patch against a newer full timeline while preserving
    unrelated work, without serializing the whole project into every undo
    group.
    """

    if source.sequence.id != destination.sequence.id:
        raise ValueError("Timeline history change belongs to different sequences")

    source_tracks, destination_tracks = _changed_entities(
        source.tracks,
        destination.tracks,
    )
    source_clips, destination_clips = _changed_entities(
        source.clips,
        destination.clips,
    )
    source_compounds, destination_compounds = _changed_entities(
        source.compounds,
        destination.compounds,
    )
    source_transitions, destination_transitions = _changed_entities(
        source.transitions,
        destination.transitions,
    )
    source_markers, destination_markers = _changed_entities(
        source.markers,
        destination.markers,
    )
    source_ranges, destination_ranges = _changed_entities(
        source.ranges,
        destination.ranges,
    )
    changed_web_state_ids = {
        item_id
        for item_id in set(source.web_states) | set(destination.web_states)
        if source.web_states.get(item_id) != destination.web_states.get(item_id)
    }
    return (
        source.model_copy(
            update={
                "tracks": source_tracks,
                "clips": source_clips,
                "compounds": source_compounds,
                "transitions": source_transitions,
                "markers": source_markers,
                "ranges": source_ranges,
                "web_states": {
                    item_id: value
                    for item_id, value in source.web_states.items()
                    if item_id in changed_web_state_ids
                },
            }
        ),
        destination.model_copy(
            update={
                "tracks": destination_tracks,
                "clips": destination_clips,
                "compounds": destination_compounds,
                "transitions": destination_transitions,
                "markers": destination_markers,
                "ranges": destination_ranges,
                "web_states": {
                    item_id: value
                    for item_id, value in destination.web_states.items()
                    if item_id in changed_web_state_ids
                },
            }
        ),
    )


def _changed_entities(
    source: Sequence[_Entity],
    destination: Sequence[_Entity],
) -> tuple[list[_Entity], list[_Entity]]:
    source_by_id = {item.id: item for item in source}
    destination_by_id = {item.id: item for item in destination}
    changed_ids = {
        item_id
        for item_id in set(source_by_id) | set(destination_by_id)
        if source_by_id.get(item_id) != destination_by_id.get(item_id)
    }
    return (
        [item for item in source if item.id in changed_ids],
        [item for item in destination if item.id in changed_ids],
    )
