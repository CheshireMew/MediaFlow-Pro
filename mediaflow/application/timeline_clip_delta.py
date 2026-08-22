from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mediaflow.application.project_changes import (
    entity_sequence_change_set,
    project_path_segment,
)
from mediaflow.domain.collaboration import ProjectChangeSet
from mediaflow.domain.timeline import Clip, TimelineState


@dataclass(frozen=True, slots=True)
class TimelineClipDelta:
    """A verified in-place clip edit with no timeline graph changes."""

    clip_ids: tuple[str, ...]
    before_clips: tuple[Clip, ...]
    after_clips: tuple[Clip, ...]

    @classmethod
    def between(
        cls,
        before: TimelineState,
        after: TimelineState,
        requested_clip_ids: set[str],
    ) -> TimelineClipDelta | None:
        if not requested_clip_ids or not _graph_is_unchanged(before, after):
            return None
        if len(before.clips) != len(after.clips):
            return None

        changed_before: list[Clip] = []
        changed_after: list[Clip] = []
        for source, destination in zip(before.clips, after.clips, strict=True):
            if source is destination:
                continue
            if source.id != destination.id:
                return None
            if source == destination:
                continue
            if source.id not in requested_clip_ids:
                return None
            changed_before.append(source)
            changed_after.append(destination)
        if not changed_before:
            return cls((), (), ())
        return cls(
            tuple(item.id for item in changed_before),
            tuple(changed_before),
            tuple(changed_after),
        )

    def history_patches(
        self,
        before: TimelineState,
        after: TimelineState,
    ) -> tuple[TimelineState, TimelineState]:
        return (
            _clip_patch(before, self.before_clips),
            _clip_patch(after, self.after_clips),
        )

    def change_set(self, sequence_id: str) -> ProjectChangeSet:
        root = f"/sequences/{project_path_segment(sequence_id)}/clips"
        return entity_sequence_change_set(root, self.before_clips, self.after_clips)


def _graph_is_unchanged(before: TimelineState, after: TimelineState) -> bool:
    return (
        _same_model(before.sequence, after.sequence)
        and _same_items(before.tracks, after.tracks)
        and _same_items(before.compounds, after.compounds)
        and _same_items(before.transitions, after.transitions)
        and _same_items(before.markers, after.markers)
        and _same_items(before.ranges, after.ranges)
        and _same_mapping(before.web_states, after.web_states)
    )


def _same_model(left: object, right: object) -> bool:
    """Use structural equality only for an object actually replaced by a mutation."""

    return left is right or left == right


def _same_items(left: Sequence[object], right: Sequence[object]) -> bool:
    """Prove an unchanged graph without deeply comparing every Pydantic model."""

    if left is right:
        return True
    if len(left) != len(right):
        return False
    return all(
        source is destination or source == destination
        for source, destination in zip(left, right, strict=True)
    )


def _same_mapping(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    if left is right:
        return True
    if left.keys() != right.keys():
        return False
    return all(left[key] is right[key] or left[key] == right[key] for key in left)


def _clip_patch(state: TimelineState, clips: tuple[Clip, ...]) -> TimelineState:
    return state.model_copy(
        update={
            "tracks": [],
            "clips": list(clips),
            "compounds": [],
            "transitions": [],
            "markers": [],
            "ranges": [],
            "web_states": {},
        }
    )
