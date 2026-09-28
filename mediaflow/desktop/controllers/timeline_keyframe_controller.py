from __future__ import annotations

import json
from typing import Any, Literal, cast, overload

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtGui import QGuiApplication

from mediaflow.domain.clip_transform_projection import project_clip_transform_keyframes
from mediaflow.domain.keyframes import KeyframeCurve, KeyframeInterpolation
from mediaflow.domain.timeline import Clip, ClipTransform, ClipTransformKeyframe
from mediaflow.domain.visual_effects import VisualEffectParameterKeyframe

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope
from .timeline_selection import selected_clip_id

_CLIPBOARD_SCHEMA = "mediaflow-native-keyframes/v1"


class TimelineKeyframeController(ControllerFacet[TimelinePresentationScope]):
    selectionChanged = Signal()

    @Property(list, notify=selectionChanged)
    def interpolationOptions(self) -> list[dict[str, str]]:
        return [
            {"label": "保持", "value": "hold"},
            {"label": "线性", "value": "linear"},
            {"label": "缓入", "value": "ease_in"},
            {"label": "缓出", "value": "ease_out"},
            {"label": "缓入缓出", "value": "ease_in_out"},
            {"label": "自定义贝塞尔", "value": "bezier"},
        ]

    @Property(list, notify=selectionChanged)
    def selectedTransformKeyframes(self) -> list[dict[str, Any]]:
        clip = self._selected_clip(required=False)
        if clip is None:
            return []
        return [self._transform_row(clip, item) for item in project_clip_transform_keyframes(clip)]

    @Property(list, notify=selectionChanged)
    def selectedEffectParameterKeyframes(self) -> list[dict[str, Any]]:
        clip = self._selected_clip(required=False)
        if clip is None:
            return []
        rows: list[dict[str, Any]] = []
        for effect in clip.visual_effects:
            for field_id, keyframes in effect.parameter_keyframes.items():
                for item in keyframes:
                    rows.append(
                        {
                            "effectId": effect.id,
                            "fieldId": field_id,
                            "timelineOffset": item.timeline_offset,
                            "timelineFrame": clip.timeline_start + item.timeline_offset,
                            "value": item.value,
                            "curve": item.curve.model_dump(mode="json"),
                        }
                    )
        return sorted(
            rows,
            key=lambda item: (
                str(item["effectId"]),
                str(item["fieldId"]),
                int(item["timelineOffset"]),
            ),
        )

    @Slot(
        str,
        int,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        str,
        float,
        float,
        float,
        float,
    )
    @report_ui_errors
    def setTransformKeyframe(
        self,
        clip_id: str,
        timeline_frame: int,
        x: float,
        y: float,
        scale_x: float,
        scale_y: float,
        rotation: float,
        crop_left: float,
        crop_top: float,
        crop_right: float,
        crop_bottom: float,
        opacity: float,
        interpolation: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> None:
        self._session._require_writable()
        clip = self._clip(clip_id)
        changed = self._session.state.binding.require_timeline().upsert_clip_transform_keyframe(
            clip_id,
            self._local_offset(clip, timeline_frame),
            ClipTransform(
                x=x,
                y=y,
                scale_x=max(0.01, scale_x),
                scale_y=max(0.01, scale_y),
                rotation=rotation,
                crop_left=crop_left,
                crop_top=crop_top,
                crop_right=crop_right,
                crop_bottom=crop_bottom,
                opacity=opacity,
            ),
            curve=self._curve(interpolation, x1, y1, x2, y2),
        )
        self._after_change(changed)
        self._session._set_status("画面关键帧已保存")

    @Slot(str, int)
    @report_ui_errors
    def removeTransformKeyframe(self, clip_id: str, timeline_offset: int) -> None:
        self._session._require_writable()
        changed = self._session.state.binding.require_timeline().remove_clip_transform_keyframe(
            clip_id,
            timeline_offset,
        )
        self._after_change(changed)
        self._session._set_status("画面关键帧已移除")

    @Slot(str, int, int)
    @report_ui_errors
    def moveTransformKeyframe(
        self,
        clip_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> None:
        self._session._require_writable()
        changed = self._session.state.binding.require_timeline().move_clip_transform_keyframe(
            clip_id,
            old_timeline_offset,
            new_timeline_offset,
        )
        self._after_change(changed)
        self._session._set_status("画面关键帧已移动")

    @Slot(str, int, int, str, float)
    @report_ui_errors
    def setTransformKeyframeChannel(
        self,
        clip_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
        channel: str,
        value: float,
    ) -> None:
        self._session._require_writable()
        allowed = {
            "x", "y", "scale_x", "scale_y", "rotation", "opacity",
            "crop_left", "crop_top", "crop_right", "crop_bottom",
        }
        if channel not in allowed:
            raise ValueError("不支持的画面关键帧通道")
        clip = self._clip(clip_id)
        if not 0 <= new_timeline_offset < clip.duration:
            raise ValueError("画面关键帧必须位于片段范围内")
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
        if channel in {"scale_x", "scale_y"}:
            value = max(0.01, value)
        if channel in {"opacity", "crop_left", "crop_top", "crop_right", "crop_bottom"}:
            value = min(1.0, max(0.0, value))
        updated = [
            item.model_copy(
                update={
                    "timeline_offset": new_timeline_offset,
                    "source_frame": None,
                    "source": "manual",
                    "transform": item.transform.model_copy(update={channel: value}),
                }
            )
            if item.timeline_offset == old_timeline_offset
            else item
            for item in keyframes
        ]
        changed = self._session.state.binding.require_timeline().set_clip_transform_keyframes(
            clip_id,
            sorted(updated, key=lambda item: int(item.timeline_offset or 0)),
        )
        self._after_change(changed)
        self._session._set_status("画面关键帧曲线点已更新")

    @Slot(str, int, float, float, float, float)
    @report_ui_errors
    def setTransformKeyframeCurve(
        self,
        clip_id: str,
        timeline_offset: int,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> None:
        self._session._require_writable()
        clip = self._clip(clip_id)
        keyframes = list(project_clip_transform_keyframes(clip))
        if not any(item.timeline_offset == timeline_offset for item in keyframes):
            raise KeyError(timeline_offset)
        curve = KeyframeCurve(
            interpolation="bezier",
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
        )
        updated = [
            item.model_copy(update={"curve": curve})
            if item.timeline_offset == timeline_offset
            else item
            for item in keyframes
        ]
        changed = self._session.state.binding.require_timeline().set_clip_transform_keyframes(
            clip_id, updated
        )
        self._after_change(changed)
        self._session._set_status("贝塞尔曲线已更新")

    @Slot(str, list, int, float)
    @report_ui_errors
    def retimeTransformKeyframes(
        self,
        clip_id: str,
        timeline_offsets: list[int],
        anchor_offset: int,
        scale: float,
    ) -> None:
        self._session._require_writable()
        changed = self._session.state.binding.require_timeline().retime_clip_transform_keyframes(
            clip_id,
            [int(value) for value in timeline_offsets],
            anchor_offset=anchor_offset,
            scale=scale,
        )
        self._after_change(changed)
        self._session._set_status("画面关键帧时间已缩放")

    @Slot(str, list)
    @report_ui_errors
    def copyTransformKeyframes(self, clip_id: str, timeline_offsets: list[int]) -> None:
        clip = self._clip(clip_id)
        selected = {int(value) for value in timeline_offsets}
        keyframes = [
            item.model_dump(mode="json")
            for item in project_clip_transform_keyframes(clip)
            if item.timeline_offset in selected
        ]
        if not keyframes:
            raise ValueError("请先选择要复制的画面关键帧")
        self._set_clipboard({"kind": "transform", "keyframes": keyframes})
        self._session._set_status("已复制 %1 个画面关键帧", len(keyframes))

    @Slot(str, int, float)
    @report_ui_errors
    def pasteTransformKeyframes(
        self,
        clip_id: str,
        timeline_frame: int,
        scale: float,
    ) -> None:
        self._session._require_writable()
        payload = self._clipboard("transform")
        clip = self._clip(clip_id)
        source = [ClipTransformKeyframe.model_validate(item) for item in payload["keyframes"]]
        source_start = min(int(item.timeline_offset or 0) for item in source)
        destination_start = self._local_offset(clip, timeline_frame)
        pasted: list[ClipTransformKeyframe] = []
        for item in source:
            offset = destination_start + round(
                (int(item.timeline_offset or 0) - source_start) * scale
            )
            if not 0 <= offset < clip.duration:
                raise ValueError("粘贴后的画面关键帧超出片段范围")
            pasted.append(
                item.model_copy(
                    update={
                        "timeline_offset": offset,
                        "source_frame": None,
                        "source": "manual",
                    }
                )
            )
        replacements = {int(item.timeline_offset or 0): item for item in pasted}
        merged = [
            item
            for item in project_clip_transform_keyframes(clip)
            if item.timeline_offset not in replacements
        ]
        merged.extend(replacements.values())
        changed = self._session.state.binding.require_timeline().set_clip_transform_keyframes(
            clip_id,
            sorted(merged, key=lambda item: item.timeline_offset or 0),
        )
        self._after_change(changed)
        self._session._set_status("已粘贴画面关键帧")

    @Slot(str, str, int, float, str, float, float, float, float)
    @report_ui_errors
    def setEffectParameterKeyframe(
        self,
        effect_id: str,
        field_id: str,
        timeline_frame: int,
        value: float,
        interpolation: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().upsert_clip_effect_parameter_keyframe(
            clip.id,
            effect_id,
            field_id,
            self._local_offset(clip, timeline_frame),
            value,
            curve=self._curve(interpolation, x1, y1, x2, y2),
        )
        self._after_change(self._clip(clip.id))
        self._session._set_status("视觉效果关键帧已保存")

    @Slot(str, str, int)
    @report_ui_errors
    def removeEffectParameterKeyframe(
        self,
        effect_id: str,
        field_id: str,
        timeline_offset: int,
    ) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().remove_clip_effect_parameter_keyframe(
            clip.id,
            effect_id,
            field_id,
            timeline_offset,
        )
        self._after_change(self._clip(clip.id))
        self._session._set_status("视觉效果关键帧已移除")

    @Slot(str, str, int, int)
    @report_ui_errors
    def moveEffectParameterKeyframe(
        self,
        effect_id: str,
        field_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().move_clip_effect_parameter_keyframe(
            clip.id,
            effect_id,
            field_id,
            old_timeline_offset,
            new_timeline_offset,
        )
        self._after_change(self._clip(clip.id))
        self._session._set_status("视觉效果关键帧已移动")

    @Slot(str, str, list, int, float)
    @report_ui_errors
    def retimeEffectParameterKeyframes(
        self,
        effect_id: str,
        field_id: str,
        timeline_offsets: list[int],
        anchor_offset: int,
        scale: float,
    ) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().retime_clip_effect_parameter_keyframes(
            clip.id,
            effect_id,
            field_id,
            [int(value) for value in timeline_offsets],
            anchor_offset=anchor_offset,
            scale=scale,
        )
        self._after_change(self._clip(clip.id))
        self._session._set_status("视觉效果关键帧时间已缩放")

    @Slot(str, str, list)
    @report_ui_errors
    def copyEffectParameterKeyframes(
        self,
        effect_id: str,
        field_id: str,
        timeline_offsets: list[int],
    ) -> None:
        clip = self._selected_clip()
        effect = next(item for item in clip.visual_effects if item.id == effect_id)
        selected = {int(value) for value in timeline_offsets}
        keyframes = [
            item.model_dump(mode="json")
            for item in effect.parameter_keyframes.get(field_id, [])
            if item.timeline_offset in selected
        ]
        if not keyframes:
            raise ValueError("请先选择要复制的视觉效果关键帧")
        self._set_clipboard(
            {
                "kind": "effect-parameter",
                "field_id": field_id,
                "keyframes": keyframes,
            }
        )
        self._session._set_status("已复制 %1 个视觉效果关键帧", len(keyframes))

    @Slot(str, str, int, float)
    @report_ui_errors
    def pasteEffectParameterKeyframes(
        self,
        effect_id: str,
        field_id: str,
        timeline_frame: int,
        scale: float,
    ) -> None:
        self._session._require_writable()
        payload = self._clipboard("effect-parameter")
        if str(payload.get("field_id")) != field_id:
            raise ValueError("剪贴板中的视觉效果参数不匹配")
        clip = self._selected_clip()
        effect = next(item for item in clip.visual_effects if item.id == effect_id)
        source = [
            VisualEffectParameterKeyframe.model_validate(item)
            for item in payload["keyframes"]
        ]
        source_start = min(item.timeline_offset for item in source)
        destination_start = self._local_offset(clip, timeline_frame)
        replacements: dict[int, VisualEffectParameterKeyframe] = {}
        for item in source:
            offset = destination_start + round(
                (item.timeline_offset - source_start) * scale
            )
            if not 0 <= offset < clip.duration:
                raise ValueError("粘贴后的视觉效果关键帧超出片段范围")
            replacements[offset] = item.model_copy(update={"timeline_offset": offset})
        merged = [
            item
            for item in effect.parameter_keyframes.get(field_id, [])
            if item.timeline_offset not in replacements
        ]
        merged.extend(replacements.values())
        self._session.state.binding.require_timeline().set_clip_effect_parameter_keyframes(
            clip.id,
            effect_id,
            field_id,
            sorted(merged, key=lambda item: item.timeline_offset),
        )
        self._after_change(self._clip(clip.id))
        self._session._set_status("已粘贴视觉效果关键帧")

    @overload
    def _selected_clip(self, *, required: Literal[True] = True) -> Clip: ...

    @overload
    def _selected_clip(self, *, required: Literal[False]) -> Clip | None: ...

    def _selected_clip(self, *, required: bool = True) -> Clip | None:
        clip_id = selected_clip_id(self._session)
        if not self._session.state.binding.timeline or not clip_id:
            if required:
                raise ValueError("请先选择一个片段")
            return None
        return self._clip(clip_id)

    def _clip(self, clip_id: str) -> Clip:
        try:
            return next(
                item
                for item in self._session.state.binding.require_timeline().state.clips
                if item.id == clip_id
            )
        except StopIteration as error:
            raise KeyError(clip_id) from error

    @staticmethod
    def _local_offset(clip: Clip, timeline_frame: int) -> int:
        offset = int(timeline_frame) - clip.timeline_start
        if not 0 <= offset < clip.duration:
            raise ValueError("播放头必须位于所选片段内")
        return offset

    @staticmethod
    def _curve(
        interpolation: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> KeyframeCurve:
        return KeyframeCurve(
            interpolation=cast(KeyframeInterpolation, interpolation),
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
        )

    @staticmethod
    def _transform_row(
        clip: Clip,
        item: ClipTransformKeyframe,
    ) -> dict[str, Any]:
        assert item.timeline_offset is not None
        return {
            "timelineOffset": item.timeline_offset,
            "timelineFrame": clip.timeline_start + item.timeline_offset,
            "source": item.source,
            "confidence": item.confidence if item.confidence is not None else -1.0,
            "curve": item.curve.model_dump(mode="json"),
            **item.transform.model_dump(mode="json"),
        }

    @staticmethod
    def _set_clipboard(payload: dict[str, Any]) -> None:
        document = {"schema": _CLIPBOARD_SCHEMA, **payload}
        QGuiApplication.clipboard().setText(
            json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        )

    @staticmethod
    def _clipboard(expected_kind: str) -> dict[str, Any]:
        try:
            value = json.loads(QGuiApplication.clipboard().text())
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("剪贴板中没有可用的 MediaFlow 关键帧") from error
        if value.get("schema") != _CLIPBOARD_SCHEMA or value.get("kind") != expected_kind:
            raise ValueError("剪贴板中的关键帧类型不匹配")
        if not isinstance(value.get("keyframes"), list) or not value["keyframes"]:
            raise ValueError("剪贴板中的关键帧为空")
        return value

    def _after_change(self, clip: Clip) -> None:
        self._session.projectors.timeline.refresh_clip_rows(
            [clip.id],
            clips=[clip],
            refresh_relations=False,
        )
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
