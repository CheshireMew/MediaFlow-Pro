from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.domain.enums import AssetKind, VisualEffectKind
from mediaflow.domain.timeline import (
    Clip,
    ClipTransform,
    ClipTransformKeyframe,
    TimelineMergeConflict,
    TimelineState,
)
from mediaflow.domain.visual_effects import ClipVisualEffect, new_visual_effect

TimelineMutation = Callable[[TimelineState], None]
TimelineCommit = Callable[[str, TimelineMutation], None]


class TimelineVisualEditing:
    """Clip transforms, tracking keyframes, and ordered visual effect chains."""

    def __init__(
        self,
        repository: TimelineEditorDocuments,
        snapshot: Callable[[], TimelineState],
        apply_change: TimelineCommit,
    ) -> None:
        self.repository = repository
        self.snapshot = snapshot
        self.apply_change = apply_change

    def set_transform(self, clip_id: str, transform: ClipTransform) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"transform": transform})

        self.apply_change("调整画面", mutate)
        return self._clip(clip_id)

    def add_effect(self, clip_id: str, kind: VisualEffectKind) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        asset = self.repository.assets.get_asset(clip.asset_id)
        if asset.kind not in {AssetKind.VIDEO, AssetKind.IMAGE}:
            raise ValueError("只有视频和图片片段可以添加视觉效果")
        effect = new_visual_effect(kind, len(clip.visual_effects))

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            source = state.clips[index]
            state.clips[index] = source.model_copy(
                update={"visual_effects": [*source.visual_effects, effect]}
            )

        self.apply_change("添加视觉效果", mutate)
        return next(item for item in self._clip(clip_id).visual_effects if item.id == effect.id)

    def update_effect(
        self,
        clip_id: str,
        effect_id: str,
        *,
        enabled: bool,
        parameters: dict[str, float],
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        source = next(item for item in clip.visual_effects if item.id == effect_id)
        updated = ClipVisualEffect.model_validate(
            {
                **source.model_dump(mode="python"),
                "enabled": enabled,
                "parameters": parameters,
            }
        )

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            current = state.clips[index]
            state.clips[index] = current.model_copy(
                update={
                    "visual_effects": [
                        updated if item.id == effect_id else item
                        for item in current.visual_effects
                    ]
                }
            )

        self.apply_change("调整视觉效果", mutate)
        return next(item for item in self._clip(clip_id).visual_effects if item.id == effect_id)

    def move_effect(
        self,
        clip_id: str,
        effect_id: str,
        position: int,
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        if not 0 <= position < len(clip.visual_effects):
            raise ValueError("视觉效果位置超出效果链")
        effects = list(clip.visual_effects)
        source_index = next(index for index, item in enumerate(effects) if item.id == effect_id)
        effect = effects.pop(source_index)
        effects.insert(position, effect)
        effects = [item.model_copy(update={"position": index}) for index, item in enumerate(effects)]

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"visual_effects": effects})

        self.apply_change("排序视觉效果", mutate)
        return next(item for item in self._clip(clip_id).visual_effects if item.id == effect_id)

    def remove_effect(self, clip_id: str, effect_id: str) -> None:
        clip = self._clip(clip_id)
        if effect_id not in {item.id for item in clip.visual_effects}:
            raise KeyError(effect_id)
        effects = [item for item in clip.visual_effects if item.id != effect_id]
        effects = [item.model_copy(update={"position": index}) for index, item in enumerate(effects)]

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(update={"visual_effects": effects})

        self.apply_change("移除视觉效果", mutate)

    def set_transform_keyframes(
        self,
        clip_id: str,
        keyframes: list[ClipTransformKeyframe],
        *,
        expected_clip: Clip | None = None,
    ) -> Clip:
        if any(item.source_frame is None for item in keyframes):
            raise ValueError("画面跟踪关键帧必须使用源帧")
        ordered = sorted(keyframes, key=lambda item: item.source_frame or 0)
        if len({item.source_frame for item in ordered}) != len(ordered):
            raise ValueError("画面跟踪关键帧不能位于同一源帧")

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            if expected_clip is not None and state.clips[index] != expected_clip:
                raise TimelineMergeConflict("clip", clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={"transform_keyframes": ordered}
            )

        self.apply_change("更新画面跟踪", mutate)
        return self._clip(clip_id)

    def _clip(self, clip_id: str) -> Clip:
        try:
            return next(item for item in self.snapshot().clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error

    @staticmethod
    def _clip_index(state: TimelineState, clip_id: str) -> int:
        try:
            return next(index for index, item in enumerate(state.clips) if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
