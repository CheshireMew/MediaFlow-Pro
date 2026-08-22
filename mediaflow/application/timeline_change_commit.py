from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mediaflow.application.edit_history import (
    ProjectEditAction,
    ProjectEditCommand,
    ProjectEditHistory,
)
from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.application.project_changes import timeline_change_set
from mediaflow.application.timeline_clip_delta import TimelineClipDelta
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.application.timeline_validator import TimelineValidator
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.timeline_history import compact_timeline_change

TimelineActionFactory = Callable[[TimelineState, TimelineState], ProjectEditAction]


@dataclass(frozen=True, slots=True)
class ClipCommitResult:
    before: TimelineState
    after: TimelineState
    committed: TimelineState | None


def commit_clip_delta(
    repository: TimelineEditorDocuments,
    validator: TimelineValidator,
    history: ProjectEditHistory,
    current: TimelineState,
    sequence_id: str,
    label: str,
    clip_ids: set[str],
    mutate: Callable[[TimelineState], None],
    timeline_action: TimelineActionFactory,
) -> ClipCommitResult:
    before = _copy_state(current)
    after = _copy_state(before)
    mutate(after)
    TimelineRules.assign_default_primary_dialogue_track(after)
    TimelineRules.normalize_sequence_in_out(after)
    TimelineRules.normalize_compounds(after)
    delta = TimelineClipDelta.between(before, after, clip_ids)
    stored_sequence = repository.sequences.get_sequence(sequence_id)
    if delta is None or stored_sequence.timeline_revision != current.sequence.timeline_revision:
        return ClipCommitResult(before, after, None)
    if not delta.clip_ids:
        return ClipCommitResult(before, after, current)
    changed_ids = set(delta.clip_ids)
    validator.validate_clip_changes(after, baseline=before, clip_ids=changed_ids)
    revision = repository.timeline.save_clip_changes(after, changed_ids)
    committed = after.model_copy(
        update={
            "sequence": after.sequence.model_copy(update={"timeline_revision": revision}),
            "clips": sorted(after.clips, key=lambda item: (item.timeline_start, item.id)),
        }
    )
    before_patch, after_patch = delta.history_patches(before, after)
    history.push(
        ProjectEditCommand(
            label=label,
            undo_actions=[timeline_action(after_patch, before_patch)],
            redo_actions=[timeline_action(before_patch, after_patch)],
        ),
        delta.change_set(sequence_id),
    )
    return ClipCommitResult(before, after, committed)


def record_full_timeline_change(
    history: ProjectEditHistory,
    label: str,
    before: TimelineState,
    after: TimelineState,
    timeline_action: TimelineActionFactory,
) -> None:
    before_patch, after_patch = compact_timeline_change(before, after)
    history.push(
        ProjectEditCommand(
            label=label,
            undo_actions=[timeline_action(after_patch, before_patch)],
            redo_actions=[timeline_action(before_patch, after_patch)],
        ),
        timeline_change_set(before, after),
    )


def _copy_state(state: TimelineState) -> TimelineState:
    return state.model_copy(
        update={
            "tracks": list(state.tracks),
            "clips": list(state.clips),
            "compounds": list(state.compounds),
            "transitions": list(state.transitions),
            "markers": list(state.markers),
            "ranges": list(state.ranges),
            "web_states": dict(state.web_states),
        }
    )


def canonical_timeline_state(state: TimelineState) -> TimelineState:
    return state.model_copy(
        update={
            "tracks": sorted(state.tracks, key=lambda item: (item.position, item.id)),
            "clips": sorted(state.clips, key=lambda item: (item.timeline_start, item.id)),
            "compounds": sorted(state.compounds, key=lambda item: item.id),
            "transitions": sorted(state.transitions, key=lambda item: item.id),
            "markers": sorted(state.markers, key=lambda item: (item.frame, item.id)),
            "ranges": sorted(state.ranges, key=lambda item: (item.start_frame, item.id)),
            "web_states": dict(sorted(state.web_states.items())),
        }
    )


copy_timeline_state = _copy_state
