from __future__ import annotations

from collections.abc import Iterable

from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.application.timeline_entities import (
    clip_by_id,
    transition_by_id,
)
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import TransitionKind
from mediaflow.domain.timeline import CompoundClip, TimelineState, Transition


class TimelineStructureEditing:
    def __init__(self, changes: TimelineChangeSession) -> None:
        self._changes = changes

    def create_compound_clip(
        self,
        clip_ids: Iterable[str],
        *,
        name: str = "复合片段",
    ) -> CompoundClip:
        selected_ids = list(dict.fromkeys(clip_ids))
        if len(selected_ids) < 2:
            raise ValueError("请至少选择两个片段来创建复合片段")
        selected = sorted(
            (clip_by_id(self._changes.current, clip_id) for clip_id in selected_ids),
            key=lambda clip: (clip.timeline_start, clip.id),
        )
        if len({clip.track_id for clip in selected}) != 1:
            raise ValueError("复合片段必须位于同一轨道")
        if any(
            left.timeline_end != right.timeline_start
            for left, right in zip(selected, selected[1:], strict=False)
        ):
            raise ValueError("复合片段中的片段必须首尾相接")
        occupied = {clip_id for item in self._changes.current.compounds for clip_id in item.clip_ids}
        if occupied.intersection(selected_ids):
            raise ValueError("所选片段已经属于其他复合片段")
        compound = CompoundClip(
            sequence_id=self._changes.sequence_id,
            name=name,
            clip_ids=[clip.id for clip in selected],
        )

        def mutate(state: TimelineState) -> None:
            state.compounds.append(compound)

        self._changes.commit_change("创建复合片段", mutate)
        return next(item for item in self._changes.current.compounds if item.id == compound.id)

    def dissolve_compound_clip(self, compound_id: str) -> None:
        if not any(item.id == compound_id for item in self._changes.current.compounds):
            raise KeyError(compound_id)

        def mutate(state: TimelineState) -> None:
            state.compounds = [item for item in state.compounds if item.id != compound_id]

        self._changes.commit_change("解除复合片段", mutate)

    def create_transition(
        self,
        left_clip_id: str,
        right_clip_id: str,
        kind: TransitionKind,
        duration: int,
    ) -> Transition:
        left = clip_by_id(self._changes.current, left_clip_id)
        right = clip_by_id(self._changes.current, right_clip_id)
        if not transition_is_available(kind, self._changes.current.sequence.profile.color_mode):
            raise ValueError("Transition is not verified for HDR10 projects")
        if left.track_id != right.track_id:
            raise ValueError("Transition clips must be on the same track")
        if left.timeline_end != right.timeline_start:
            raise ValueError("Transition clips must be adjacent")
        if duration > min(left.duration, right.duration):
            raise ValueError("Transition duration exceeds a source clip")
        transition = Transition(
            track_id=left.track_id,
            left_clip_id=left.id,
            right_clip_id=right.id,
            kind=kind,
            duration=duration,
        )

        def mutate(state: TimelineState) -> None:
            state.transitions = [
                item
                for item in state.transitions
                if not (item.left_clip_id == left.id and item.right_clip_id == right.id)
            ]
            state.transitions.append(transition)

        self._changes.commit_change("添加转场", mutate)
        return transition

    def update_transition(
        self,
        transition_id: str,
        *,
        kind: TransitionKind,
        duration: int,
        parameters: dict | None = None,
    ) -> Transition:
        source = transition_by_id(self._changes.current, transition_id)
        if not transition_is_available(kind, self._changes.current.sequence.profile.color_mode):
            raise ValueError("Transition is not verified for HDR10 projects")
        left = clip_by_id(self._changes.current, source.left_clip_id)
        right = clip_by_id(self._changes.current, source.right_clip_id)
        if duration <= 0 or duration > min(left.duration, right.duration):
            raise ValueError("Transition duration exceeds the available clips")

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.transitions) if item.id == transition_id)
            state.transitions[index] = source.model_copy(
                update={
                    "kind": kind,
                    "duration": duration,
                    "parameters": source.parameters if parameters is None else parameters,
                }
            )

        self._changes.commit_change("调整转场", mutate)
        return transition_by_id(self._changes.current, transition_id)

    def remove_transition(self, transition_id: str) -> None:
        transition_by_id(self._changes.current, transition_id)

        def mutate(state: TimelineState) -> None:
            state.transitions = [item for item in state.transitions if item.id != transition_id]

        self._changes.commit_change("移除转场", mutate)

    def replace_contents(
        self,
        destination: TimelineState,
        *,
        label: str = "导入可携带时间线",
    ) -> TimelineState:
        if destination.sequence.id != self._changes.sequence_id:
            raise ValueError("Timeline replacement belongs to another sequence")
        if destination.sequence.profile != self._changes.current.sequence.profile:
            raise ValueError("Set the sequence profile before replacing timeline contents")

        def mutate(state: TimelineState) -> None:
            state.tracks = list(destination.tracks)
            state.clips = list(destination.clips)
            state.compounds = list(destination.compounds)
            state.transitions = list(destination.transitions)
            state.markers = list(destination.markers)
            state.ranges = list(destination.ranges)
            state.web_states = dict(destination.web_states)

        self._changes.commit_change(label, mutate, allow_locked_changes=True)
        return self._changes.snapshot
