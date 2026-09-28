from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.domain.clip_transform_projection import project_clip_transform_keyframes
from mediaflow.domain.enums import AssetKind, MaskShapeKind, VisualEffectKind
from mediaflow.domain.keyframes import KeyframeCurve
from mediaflow.domain.mask_projection import project_mask_keyframes
from mediaflow.domain.masks import ClipMask, MaskGeometry, MaskKeyframe
from mediaflow.domain.timeline import (
    Clip,
    ClipTransform,
    ClipTransformKeyframe,
    TimelineMergeConflict,
    TimelineState,
)
from mediaflow.domain.visual_effects import (
    VISUAL_EFFECT_DEFINITIONS,
    ClipVisualEffect,
    VisualEffectParameterKeyframe,
    new_visual_effect,
)

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

    def add_effect(
        self,
        clip_id: str,
        kind: VisualEffectKind,
        *,
        resource_asset_id: str | None = None,
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        asset = self.repository.assets.get_asset(clip.asset_id)
        if asset.kind not in {AssetKind.VIDEO, AssetKind.IMAGE}:
            raise ValueError("只有视频和图片片段可以添加视觉效果")
        definition = VISUAL_EFFECT_DEFINITIONS[kind]
        if definition.resource_asset_kind is not None:
            if not resource_asset_id:
                raise ValueError("这个视觉效果需要先选择资源文件")
            resource_asset = self.repository.assets.get_asset(resource_asset_id)
            if resource_asset.kind != definition.resource_asset_kind:
                raise ValueError("视觉效果资源类型不匹配")
        elif resource_asset_id is not None:
            raise ValueError("这个视觉效果不接受资源文件")
        effect = new_visual_effect(
            kind,
            len(clip.visual_effects),
            resource_asset_id=resource_asset_id,
        )

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

    def assign_effect_mask(
        self,
        clip_id: str,
        effect_id: str,
        mask_id: str | None,
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        effect = self._effect(clip, effect_id)
        if mask_id is not None:
            self._mask(clip, mask_id)
        updated = effect.model_copy(update={"mask_id": mask_id})

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

        self.apply_change("设置局部效果蒙版", mutate)
        return self._effect(self._clip(clip_id), effect_id)

    def add_mask(
        self,
        clip_id: str,
        kind: MaskShapeKind,
        *,
        name: str,
        geometry: MaskGeometry,
        combine_mode: str = "replace",
    ) -> ClipMask:
        clip = self._clip(clip_id)
        asset = self.repository.assets.get_asset(clip.asset_id)
        if asset.kind not in {AssetKind.VIDEO, AssetKind.IMAGE}:
            raise ValueError("只有视频和图片片段可以添加蒙版")
        mask = ClipMask(
            name=name.strip(),
            kind=kind,
            position=len(clip.masks),
            geometry=geometry,
            combine_mode=cast(
                Literal["replace", "add", "subtract", "intersect"],
                combine_mode,
            ),
        )

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            current = state.clips[index]
            state.clips[index] = current.model_copy(
                update={"masks": [*current.masks, mask]}
            )

        self.apply_change("添加蒙版", mutate)
        return self._mask(self._clip(clip_id), mask.id)

    def update_mask(
        self,
        clip_id: str,
        mask_id: str,
        *,
        name: str,
        enabled: bool,
        inverted: bool,
        feather: int,
        feather_passes: int,
        opacity: float,
        geometry: MaskGeometry,
        combine_mode: str = "replace",
    ) -> ClipMask:
        clip = self._clip(clip_id)
        source = self._mask(clip, mask_id)
        updated = ClipMask.model_validate(
            {
                **source.model_dump(mode="python"),
                "name": name.strip(),
                "enabled": enabled,
                "combine_mode": combine_mode,
                "inverted": inverted,
                "feather": feather,
                "feather_passes": feather_passes,
                "opacity": opacity,
                "geometry": geometry,
            }
        )
        return self._replace_mask(clip_id, updated, "调整蒙版")

    def move_mask(self, clip_id: str, mask_id: str, position: int) -> ClipMask:
        clip = self._clip(clip_id)
        moving = self._mask(clip, mask_id)
        ordered = sorted(clip.masks, key=lambda item: item.position)
        ordered = [item for item in ordered if item.id != mask_id]
        target = min(max(0, position), len(ordered))
        ordered.insert(target, moving)
        reordered = [
            item.model_copy(update={"position": index})
            for index, item in enumerate(ordered)
        ]

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={"masks": reordered}
            )

        self.apply_change("调整蒙版顺序", mutate)
        return self._mask(self._clip(clip_id), mask_id)

    def remove_mask(self, clip_id: str, mask_id: str) -> None:
        clip = self._clip(clip_id)
        self._mask(clip, mask_id)
        remaining = [item for item in clip.masks if item.id != mask_id]
        remaining = [
            item.model_copy(update={"position": index})
            for index, item in enumerate(remaining)
        ]

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            current = state.clips[index]
            state.clips[index] = current.model_copy(
                update={
                    "masks": remaining,
                    "visual_effects": [
                        (
                            effect.model_copy(update={"mask_id": None})
                            if effect.mask_id == mask_id
                            else effect
                        )
                        for effect in current.visual_effects
                    ],
                }
            )

        self.apply_change("移除蒙版", mutate)

    def upsert_mask_keyframe(
        self,
        clip_id: str,
        mask_id: str,
        timeline_offset: int,
        geometry: MaskGeometry,
        *,
        curve: KeyframeCurve | None = None,
    ) -> ClipMask:
        clip = self._clip(clip_id)
        self._require_clip_offset(clip, timeline_offset)
        mask = self._mask(clip, mask_id)
        keyframes = [
            item
            for item in project_mask_keyframes(clip, mask)
            if item.timeline_offset != timeline_offset
        ]
        keyframes.append(
            MaskKeyframe(
                timeline_offset=timeline_offset,
                geometry=geometry,
                curve=curve or KeyframeCurve(),
            )
        )
        return self._replace_mask_keyframes(
            clip_id,
            mask,
            sorted(keyframes, key=lambda item: item.timeline_offset or 0),
            "设置蒙版关键帧",
        )

    def remove_mask_keyframe(
        self,
        clip_id: str,
        mask_id: str,
        timeline_offset: int,
    ) -> ClipMask:
        clip = self._clip(clip_id)
        mask = self._mask(clip, mask_id)
        keyframes = list(project_mask_keyframes(clip, mask))
        remaining = [item for item in keyframes if item.timeline_offset != timeline_offset]
        if len(remaining) == len(keyframes):
            raise KeyError(timeline_offset)
        return self._replace_mask_keyframes(
            clip_id,
            mask,
            remaining,
            "移除蒙版关键帧",
        )

    def move_mask_keyframe(
        self,
        clip_id: str,
        mask_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> ClipMask:
        clip = self._clip(clip_id)
        self._require_clip_offset(clip, new_timeline_offset)
        mask = self._mask(clip, mask_id)
        keyframes = list(project_mask_keyframes(clip, mask))
        moving = next(
            (item for item in keyframes if item.timeline_offset == old_timeline_offset),
            None,
        )
        if moving is None:
            raise KeyError(old_timeline_offset)
        if old_timeline_offset != new_timeline_offset and any(
            item.timeline_offset == new_timeline_offset for item in keyframes
        ):
            raise ValueError("目标帧已经存在蒙版关键帧")
        updated = [
            (
                moving.model_copy(update={"timeline_offset": new_timeline_offset})
                if item.timeline_offset == old_timeline_offset
                else item
            )
            for item in keyframes
        ]
        return self._replace_mask_keyframes(
            clip_id,
            mask,
            sorted(updated, key=lambda item: item.timeline_offset or 0),
            "移动蒙版关键帧",
        )

    def retime_mask_keyframes(
        self,
        clip_id: str,
        mask_id: str,
        timeline_offsets: list[int],
        *,
        anchor_offset: int,
        scale: float,
    ) -> ClipMask:
        if scale <= 0:
            raise ValueError("关键帧时间缩放必须大于零")
        clip = self._clip(clip_id)
        mask = self._mask(clip, mask_id)
        keyframes = list(project_mask_keyframes(clip, mask))
        selected = set(timeline_offsets)
        if not selected:
            raise ValueError("请至少选择一个蒙版关键帧")
        available = {int(item.timeline_offset or 0) for item in keyframes}
        missing = selected - available
        if missing:
            raise KeyError(min(missing))
        retimed = []
        for item in keyframes:
            offset = int(item.timeline_offset or 0)
            if offset in selected:
                offset = round(anchor_offset + (offset - anchor_offset) * scale)
                self._require_clip_offset(clip, offset)
                item = item.model_copy(update={"timeline_offset": offset})
            retimed.append(item)
        offsets = [int(item.timeline_offset or 0) for item in retimed]
        if len(offsets) != len(set(offsets)):
            raise ValueError("时间缩放会让多个蒙版关键帧重叠")
        return self._replace_mask_keyframes(
            clip_id,
            mask,
            sorted(retimed, key=lambda item: item.timeline_offset or 0),
            "缩放蒙版关键帧时间",
        )

    def set_mask_keyframes(
        self,
        clip_id: str,
        mask_id: str,
        keyframes: list[MaskKeyframe],
        *,
        expected_clip: Clip | None = None,
    ) -> ClipMask:
        modes = {
            "timeline" if item.timeline_offset is not None else "source"
            for item in keyframes
        }
        if len(modes) > 1:
            raise ValueError("蒙版关键帧不能混合源帧和时间线帧")
        clip = self._clip(clip_id)
        self._mask(clip, mask_id)
        if modes == {"timeline"}:
            for item in keyframes:
                assert item.timeline_offset is not None
                self._require_clip_offset(clip, item.timeline_offset)
            ordered = sorted(keyframes, key=lambda item: item.timeline_offset or 0)
            anchors = [item.timeline_offset for item in ordered]
            label = "更新蒙版关键帧"
        else:
            ordered = sorted(keyframes, key=lambda item: item.source_frame or 0)
            anchors = [item.source_frame for item in ordered]
            label = "更新蒙版跟踪"
        if len(set(anchors)) != len(ordered):
            raise ValueError("蒙版关键帧不能位于同一帧")

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            if expected_clip is not None and state.clips[index] != expected_clip:
                raise TimelineMergeConflict("clip", clip_id)
            current = state.clips[index]
            state.clips[index] = current.model_copy(
                update={
                    "masks": [
                        item.model_copy(update={"keyframes": ordered})
                        if item.id == mask_id
                        else item
                        for item in current.masks
                    ]
                }
            )

        self.apply_change(label, mutate)
        return self._mask(self._clip(clip_id), mask_id)

    def upsert_transform_keyframe(
        self,
        clip_id: str,
        timeline_offset: int,
        transform: ClipTransform,
        *,
        curve: KeyframeCurve | None = None,
    ) -> Clip:
        clip = self._clip(clip_id)
        self._require_clip_offset(clip, timeline_offset)
        keyframes = list(project_clip_transform_keyframes(clip))
        keyframe = ClipTransformKeyframe(
            timeline_offset=timeline_offset,
            transform=transform,
            source="manual",
            curve=curve or KeyframeCurve(),
        )
        keyframes = [
            item for item in keyframes if item.timeline_offset != timeline_offset
        ]
        keyframes.append(keyframe)
        return self._replace_transform_keyframes(
            clip_id,
            sorted(keyframes, key=lambda item: item.timeline_offset or 0),
            "设置画面关键帧",
        )

    def remove_transform_keyframe(self, clip_id: str, timeline_offset: int) -> Clip:
        clip = self._clip(clip_id)
        keyframes = list(project_clip_transform_keyframes(clip))
        remaining = [
            item for item in keyframes if item.timeline_offset != timeline_offset
        ]
        if len(remaining) == len(keyframes):
            raise KeyError(timeline_offset)
        return self._replace_transform_keyframes(
            clip_id,
            remaining,
            "移除画面关键帧",
        )

    def move_transform_keyframe(
        self,
        clip_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> Clip:
        clip = self._clip(clip_id)
        self._require_clip_offset(clip, new_timeline_offset)
        keyframes = list(project_clip_transform_keyframes(clip))
        moving = next(
            (item for item in keyframes if item.timeline_offset == old_timeline_offset),
            None,
        )
        if moving is None:
            raise KeyError(old_timeline_offset)
        if old_timeline_offset != new_timeline_offset and any(
            item.timeline_offset == new_timeline_offset for item in keyframes
        ):
            raise ValueError("目标帧已经存在画面关键帧")
        updated = [
            (
                moving.model_copy(update={"timeline_offset": new_timeline_offset})
                if item.timeline_offset == old_timeline_offset
                else item
            )
            for item in keyframes
        ]
        return self._replace_transform_keyframes(
            clip_id,
            sorted(updated, key=lambda item: item.timeline_offset or 0),
            "移动画面关键帧",
        )

    def retime_transform_keyframes(
        self,
        clip_id: str,
        timeline_offsets: list[int],
        *,
        anchor_offset: int,
        scale: float,
    ) -> Clip:
        if scale <= 0:
            raise ValueError("关键帧时间缩放必须大于零")
        clip = self._clip(clip_id)
        selected = set(timeline_offsets)
        if not selected:
            raise ValueError("请至少选择一个画面关键帧")
        keyframes = list(project_clip_transform_keyframes(clip))
        available = {int(item.timeline_offset or 0) for item in keyframes}
        missing = selected - available
        if missing:
            raise KeyError(min(missing))
        retimed = []
        for item in keyframes:
            offset = int(item.timeline_offset or 0)
            if offset in selected:
                offset = round(anchor_offset + (offset - anchor_offset) * scale)
                self._require_clip_offset(clip, offset)
                item = item.model_copy(update={"timeline_offset": offset})
            retimed.append(item)
        offsets = [int(item.timeline_offset or 0) for item in retimed]
        if len(offsets) != len(set(offsets)):
            raise ValueError("时间缩放会让多个画面关键帧重叠")
        return self._replace_transform_keyframes(
            clip_id,
            sorted(retimed, key=lambda item: item.timeline_offset or 0),
            "缩放画面关键帧时间",
        )

    def upsert_effect_parameter_keyframe(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        timeline_offset: int,
        value: float,
        *,
        curve: KeyframeCurve | None = None,
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        self._require_clip_offset(clip, timeline_offset)
        effect = self._effect(clip, effect_id)
        keyframes = dict(effect.parameter_keyframes)
        field_keyframes = [
            item
            for item in keyframes.get(field_id, [])
            if item.timeline_offset != timeline_offset
        ]
        field_keyframes.append(
            VisualEffectParameterKeyframe(
                timeline_offset=timeline_offset,
                value=value,
                curve=curve or KeyframeCurve(),
            )
        )
        keyframes[field_id] = sorted(
            field_keyframes,
            key=lambda item: item.timeline_offset,
        )
        return self._replace_effect_keyframes(
            clip_id,
            effect,
            keyframes,
            "设置视觉效果关键帧",
        )

    def set_effect_parameter_keyframes(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        field_keyframes: list[VisualEffectParameterKeyframe],
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        for item in field_keyframes:
            self._require_clip_offset(clip, item.timeline_offset)
        offsets = [item.timeline_offset for item in field_keyframes]
        if len(offsets) != len(set(offsets)):
            raise ValueError("视觉效果关键帧不能位于同一帧")
        effect = self._effect(clip, effect_id)
        keyframes = dict(effect.parameter_keyframes)
        if field_keyframes:
            keyframes[field_id] = sorted(
                field_keyframes,
                key=lambda item: item.timeline_offset,
            )
        else:
            keyframes.pop(field_id, None)
        return self._replace_effect_keyframes(
            clip_id,
            effect,
            keyframes,
            "更新视觉效果关键帧",
        )

    def remove_effect_parameter_keyframe(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        timeline_offset: int,
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        effect = self._effect(clip, effect_id)
        keyframes = dict(effect.parameter_keyframes)
        source = keyframes.get(field_id, [])
        remaining = [
            item for item in source if item.timeline_offset != timeline_offset
        ]
        if len(remaining) == len(source):
            raise KeyError(timeline_offset)
        if remaining:
            keyframes[field_id] = remaining
        else:
            keyframes.pop(field_id, None)
        return self._replace_effect_keyframes(
            clip_id,
            effect,
            keyframes,
            "移除视觉效果关键帧",
        )

    def move_effect_parameter_keyframe(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> ClipVisualEffect:
        clip = self._clip(clip_id)
        self._require_clip_offset(clip, new_timeline_offset)
        effect = self._effect(clip, effect_id)
        keyframes = dict(effect.parameter_keyframes)
        source = keyframes.get(field_id, [])
        moving = next(
            (item for item in source if item.timeline_offset == old_timeline_offset),
            None,
        )
        if moving is None:
            raise KeyError(old_timeline_offset)
        if old_timeline_offset != new_timeline_offset and any(
            item.timeline_offset == new_timeline_offset for item in source
        ):
            raise ValueError("目标帧已经存在视觉效果关键帧")
        keyframes[field_id] = sorted(
            [
                (
                    moving.model_copy(update={"timeline_offset": new_timeline_offset})
                    if item.timeline_offset == old_timeline_offset
                    else item
                )
                for item in source
            ],
            key=lambda item: item.timeline_offset,
        )
        return self._replace_effect_keyframes(
            clip_id,
            effect,
            keyframes,
            "移动视觉效果关键帧",
        )

    def retime_effect_parameter_keyframes(
        self,
        clip_id: str,
        effect_id: str,
        field_id: str,
        timeline_offsets: list[int],
        *,
        anchor_offset: int,
        scale: float,
    ) -> ClipVisualEffect:
        if scale <= 0:
            raise ValueError("关键帧时间缩放必须大于零")
        clip = self._clip(clip_id)
        effect = self._effect(clip, effect_id)
        keyframes = dict(effect.parameter_keyframes)
        source = keyframes.get(field_id, [])
        selected = set(timeline_offsets)
        if not selected:
            raise ValueError("请至少选择一个视觉效果关键帧")
        available = {item.timeline_offset for item in source}
        missing = selected - available
        if missing:
            raise KeyError(min(missing))
        retimed = []
        for item in source:
            offset = item.timeline_offset
            if offset in selected:
                offset = round(anchor_offset + (offset - anchor_offset) * scale)
                self._require_clip_offset(clip, offset)
                item = item.model_copy(update={"timeline_offset": offset})
            retimed.append(item)
        offsets = [item.timeline_offset for item in retimed]
        if len(offsets) != len(set(offsets)):
            raise ValueError("时间缩放会让多个视觉效果关键帧重叠")
        keyframes[field_id] = sorted(
            retimed,
            key=lambda item: item.timeline_offset,
        )
        return self._replace_effect_keyframes(
            clip_id,
            effect,
            keyframes,
            "缩放视觉效果关键帧时间",
        )

    def set_transform_keyframes(
        self,
        clip_id: str,
        keyframes: list[ClipTransformKeyframe],
        *,
        expected_clip: Clip | None = None,
    ) -> Clip:
        modes = {
            "timeline" if item.timeline_offset is not None else "source"
            for item in keyframes
        }
        if len(modes) > 1:
            raise ValueError("画面关键帧不能混合源帧和时间线帧")
        if modes == {"timeline"}:
            clip = self._clip(clip_id)
            for item in keyframes:
                assert item.timeline_offset is not None
                self._require_clip_offset(clip, item.timeline_offset)
            ordered = sorted(keyframes, key=lambda item: item.timeline_offset or 0)
            anchors = [item.timeline_offset for item in ordered]
            label = "更新画面关键帧"
        else:
            ordered = sorted(keyframes, key=lambda item: item.source_frame or 0)
            anchors = [item.source_frame for item in ordered]
            label = "更新画面跟踪"
        if len(set(anchors)) != len(ordered):
            raise ValueError("画面关键帧不能位于同一帧")

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            if expected_clip is not None and state.clips[index] != expected_clip:
                raise TimelineMergeConflict("clip", clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={"transform_keyframes": ordered}
            )

        self.apply_change(label, mutate)
        return self._clip(clip_id)

    def _replace_transform_keyframes(
        self,
        clip_id: str,
        keyframes: list[ClipTransformKeyframe],
        label: str,
    ) -> Clip:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            state.clips[index] = state.clips[index].model_copy(
                update={"transform_keyframes": keyframes}
            )

        self.apply_change(label, mutate)
        return self._clip(clip_id)

    def _replace_effect_keyframes(
        self,
        clip_id: str,
        effect: ClipVisualEffect,
        keyframes: dict[str, list[VisualEffectParameterKeyframe]],
        label: str,
    ) -> ClipVisualEffect:
        updated = ClipVisualEffect.model_validate(
            {
                **effect.model_dump(mode="python"),
                "parameter_keyframes": keyframes,
            }
        )

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            clip = state.clips[index]
            state.clips[index] = clip.model_copy(
                update={
                    "visual_effects": [
                        updated if item.id == effect.id else item
                        for item in clip.visual_effects
                    ]
                }
            )

        self.apply_change(label, mutate)
        return self._effect(self._clip(clip_id), effect.id)

    def _replace_mask_keyframes(
        self,
        clip_id: str,
        mask: ClipMask,
        keyframes: list[MaskKeyframe],
        label: str,
    ) -> ClipMask:
        return self._replace_mask(
            clip_id,
            mask.model_copy(update={"keyframes": keyframes}),
            label,
        )

    def _replace_mask(
        self,
        clip_id: str,
        mask: ClipMask,
        label: str,
    ) -> ClipMask:
        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            clip = state.clips[index]
            state.clips[index] = clip.model_copy(
                update={
                    "masks": [mask if item.id == mask.id else item for item in clip.masks]
                }
            )

        self.apply_change(label, mutate)
        return self._mask(self._clip(clip_id), mask.id)

    @staticmethod
    def _effect(clip: Clip, effect_id: str) -> ClipVisualEffect:
        try:
            return next(item for item in clip.visual_effects if item.id == effect_id)
        except StopIteration as error:
            raise KeyError(effect_id) from error

    @staticmethod
    def _mask(clip: Clip, mask_id: str) -> ClipMask:
        try:
            return next(item for item in clip.masks if item.id == mask_id)
        except StopIteration as error:
            raise KeyError(mask_id) from error

    @staticmethod
    def _require_clip_offset(clip: Clip, timeline_offset: int) -> None:
        if not 0 <= timeline_offset < clip.duration:
            raise ValueError("关键帧必须位于片段范围内")
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
