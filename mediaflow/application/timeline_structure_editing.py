from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.domain.effect_registry import transition_is_available
from mediaflow.domain.enums import TransitionKind
from mediaflow.domain.timeline import Clip, CompoundClip, TimelineState, Transition


class TimelineStructureEditing:
    sequence_id: str
    _changes: TimelineChangeSession

    if TYPE_CHECKING:
        def _commit(
            self,
            label: str,
            mutate: Callable[[TimelineState], None],
            *,
            allow_locked_changes: bool = False,
        ) -> None: ...

        def _clip(self, clip_id: str) -> Clip: ...

        def _transition(self, transition_id: str) -> Transition: ...
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
            (self._clip(clip_id) for clip_id in selected_ids),
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
            sequence_id=self.sequence_id,
            name=name,
            clip_ids=[clip.id for clip in selected],
        )

        def mutate(state: TimelineState) -> None:
            state.compounds.append(compound)

        self._commit("创建复合片段", mutate)
        return next(item for item in self._changes.current.compounds if item.id == compound.id)

    def dissolve_compound_clip(self, compound_id: str) -> None:
        if not any(item.id == compound_id for item in self._changes.current.compounds):
            raise KeyError(compound_id)

        def mutate(state: TimelineState) -> None:
            state.compounds = [item for item in state.compounds if item.id != compound_id]

        self._commit("解除复合片段", mutate)

    def create_transition(
        self,
        left_clip_id: str,
        right_clip_id: str,
        kind: TransitionKind,
        duration: int,
    ) -> Transition:
        left = self._clip(left_clip_id)
        right = self._clip(right_clip_id)
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

        self._commit("添加转场", mutate)
        return transition

    def update_transition(
        self,
        transition_id: str,
        *,
        kind: TransitionKind,
        duration: int,
        parameters: dict | None = None,
    ) -> Transition:
        source = self._transition(transition_id)
        if not transition_is_available(kind, self._changes.current.sequence.profile.color_mode):
            raise ValueError("Transition is not verified for HDR10 projects")
        left = self._clip(source.left_clip_id)
        right = self._clip(source.right_clip_id)
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

        self._commit("调整转场", mutate)
        return self._transition(transition_id)

    def remove_transition(self, transition_id: str) -> None:
        self._transition(transition_id)

        def mutate(state: TimelineState) -> None:
            state.transitions = [item for item in state.transitions if item.id != transition_id]

        self._commit("移除转场", mutate)
