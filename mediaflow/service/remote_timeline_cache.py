from __future__ import annotations

from typing import Any

from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.enums import ClipMediaKind
from mediaflow.domain.timeline import Clip, TimelineState

_LOCALLY_PROJECTED_WRITES = {
    "move_clip",
    "move_clips",
    "set_clip_audio",
    "set_clip_speed",
    "set_clip_transform",
    "set_clips_properties",
    "trim_clip",
}


def project_timeline_write(
    state: TimelineState,
    command: str,
    result: Any,
) -> TimelineState | None:
    """Apply a safe write result to a remote snapshot, or require a reload."""

    changed = result if isinstance(result, list) else [result]
    if (
        command not in _LOCALLY_PROJECTED_WRITES
        or not changed
        or not all(isinstance(item, Clip) for item in changed)
    ):
        return None
    replacements = {item.id: item for item in changed}
    clips = [replacements.get(item.id, item) for item in state.clips]
    if command in {"move_clip", "move_clips", "trim_clip"}:
        compound_clip_ids = {
            clip_id for compound in state.compounds for clip_id in compound.clip_ids
        }
        if compound_clip_ids.intersection(replacements):
            return None
    if command in {"move_clip", "move_clips"}:
        tracks = {track.id: track for track in state.tracks}
        if any(
            item.media_kind == ClipMediaKind.LINKED_AV
            and not tracks[item.track_id].linked_audio_track_id
            for item in changed
        ):
            return None
    update: dict[str, Any] = {"clips": clips}
    if command in {"move_clip", "move_clips", "trim_clip"}:
        clips_by_id = {item.id: item for item in clips}
        update["transitions"] = [
            item
            for item in state.transitions
            if TimelineRules.transition_is_valid(item, clips_by_id)
        ]
    return state.model_copy(update=update)
