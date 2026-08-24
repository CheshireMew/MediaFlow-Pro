from __future__ import annotations

from typing import Any

from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.enums import ClipMediaKind
from mediaflow.domain.timeline import Clip, TimelineState

_LOCALLY_PROJECTED_WRITES = {
    "copy_clip",
    "delete_clip",
    "delete_clips",
    "move_clip",
    "move_clips",
    "set_clip_audio",
    "set_clip_speed",
    "set_clip_transform",
    "set_clips_properties",
    "split_clip",
    "trim_clip",
}


def project_timeline_write(
    state: TimelineState,
    command: str,
    result: Any,
    *,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> TimelineState | None:
    """Apply a safe write result to a remote snapshot, or require a reload."""

    call_kwargs = kwargs or {}
    if command == "copy_clip":
        if not isinstance(result, Clip) or not args:
            return None
        source_id = str(args[0])
        source = next((item for item in state.clips if item.id == source_id), None)
        if source is None:
            return None
        tracks = {track.id: track for track in state.tracks}
        if (
            result.media_kind == ClipMediaKind.LINKED_AV
            and not tracks[result.track_id].linked_audio_track_id
        ):
            return None
        web_states = dict(state.web_states)
        if source_id in web_states:
            web_states[result.id] = web_states[source_id].model_copy(
                update={"clip_id": result.id, "revision": 0}
            )
        return state.model_copy(
            update={"clips": [*state.clips, result], "web_states": web_states}
        )

    if command == "split_clip":
        if (
            not isinstance(result, (list, tuple))
            or len(result) != 2
            or not all(isinstance(item, Clip) for item in result)
            or not args
        ):
            return None
        source_id = str(args[0])
        source_index = next(
            (index for index, item in enumerate(state.clips) if item.id == source_id),
            None,
        )
        if source_index is None:
            return None
        left, right = result
        clips = [*state.clips]
        clips[source_index : source_index + 1] = [left, right]
        clips_by_id = {item.id: item for item in clips}
        transitions = []
        for transition in state.transitions:
            candidate = (
                transition.model_copy(update={"left_clip_id": right.id})
                if transition.left_clip_id == source_id
                else transition
            )
            if TimelineRules.transition_is_valid(candidate, clips_by_id):
                transitions.append(candidate)
        web_states = dict(state.web_states)
        if source_id in web_states:
            web_states[right.id] = web_states[source_id].model_copy(
                update={"clip_id": right.id, "revision": 0}
            )
        return state.model_copy(
            update={
                "clips": clips,
                "transitions": transitions,
                "web_states": web_states,
            }
        )

    if command in {"delete_clip", "delete_clips"}:
        if not args or bool(call_kwargs.get("ripple", False)):
            return None
        selected_ids = (
            {str(args[0])}
            if command == "delete_clip"
            else {str(item) for item in args[0]}
        )
        return state.model_copy(
            update={
                "clips": [item for item in state.clips if item.id not in selected_ids],
                "transitions": [
                    item
                    for item in state.transitions
                    if item.left_clip_id not in selected_ids
                    and item.right_clip_id not in selected_ids
                ],
                "web_states": {
                    clip_id: web_state
                    for clip_id, web_state in state.web_states.items()
                    if clip_id not in selected_ids
                },
            }
        )

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
