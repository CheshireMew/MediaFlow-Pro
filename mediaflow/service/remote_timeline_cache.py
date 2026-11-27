from __future__ import annotations

from mediaflow.application.timeline_change_commit import canonical_timeline_state
from mediaflow.application.timeline_merge import TimelineMergePolicy
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.timeline_history import TimelineStateChange


def apply_timeline_change(state: TimelineState, change: TimelineStateChange) -> TimelineState:
    """Apply committed entity values without reimplementing editing rules."""

    if not state.sequence.id == change.before.sequence.id == change.after.sequence.id:
        raise ValueError("Committed timeline change belongs to another sequence")
    merged = TimelineMergePolicy.merge(change.before, change.after, state)
    return canonical_timeline_state(merged.model_copy(update={"sequence": change.after.sequence}))
