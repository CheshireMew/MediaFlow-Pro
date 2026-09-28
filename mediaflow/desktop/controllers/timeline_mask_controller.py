from __future__ import annotations

import json
from typing import Any, Literal, cast, overload

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.domain.enums import MaskShapeKind
from mediaflow.domain.keyframes import KeyframeCurve, KeyframeInterpolation
from mediaflow.domain.mask_projection import project_mask_keyframes
from mediaflow.domain.masks import MaskGeometry, MaskPoint
from mediaflow.domain.task_commands import TrackMaskCommand
from mediaflow.domain.timeline import Clip

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope
from .timeline_selection import selected_clip_id


class TimelineMaskController(ControllerFacet[TimelinePresentationScope]):
    selectionChanged = Signal()

    @Property(list, notify=selectionChanged)
    def shapeOptions(self) -> list[dict[str, str]]:
        return [
            {"label": "矩形", "value": MaskShapeKind.RECTANGLE.value},
            {"label": "椭圆", "value": MaskShapeKind.ELLIPSE.value},
            {"label": "多边形", "value": MaskShapeKind.POLYGON.value},
            {"label": "贝塞尔路径", "value": MaskShapeKind.BEZIER.value},
        ]

    @Property(list, notify=selectionChanged)
    def combineOptions(self) -> list[dict[str, str]]:
        return [
            {"label": "替换", "value": "replace"},
            {"label": "添加", "value": "add"},
            {"label": "减去", "value": "subtract"},
            {"label": "交集", "value": "intersect"},
        ]

    @Property(list, notify=selectionChanged)
    def selectedMasks(self) -> list[dict[str, Any]]:
        clip = self._selected_clip(required=False)
        if clip is None:
            return []
        rows: list[dict[str, Any]] = []
        for mask in clip.masks:
            keyframes = []
            for item in project_mask_keyframes(clip, mask):
                assert item.timeline_offset is not None
                keyframes.append(
                    {
                        "timelineOffset": item.timeline_offset,
                        "timelineFrame": clip.timeline_start + item.timeline_offset,
                        "source": item.source,
                        "confidence": item.confidence if item.confidence is not None else -1.0,
                        "curve": item.curve.model_dump(mode="json"),
                        **self._geometry_row(item.geometry),
                    }
                )
            rows.append(
                {
                    "maskId": mask.id,
                    "name": mask.name,
                    "kind": mask.kind.value,
                    "position": mask.position,
                    "enabled": mask.enabled,
                    "combineMode": mask.combine_mode,
                    "inverted": mask.inverted,
                    "feather": mask.feather,
                    "featherPasses": mask.feather_passes,
                    "opacity": mask.opacity,
                    "keyframes": keyframes,
                    **self._geometry_row(mask.geometry),
                }
            )
        return rows

    @Slot(str)
    @report_ui_errors
    def addSelectedClipMask(self, kind: str) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        shape = MaskShapeKind(kind)
        geometry = MaskGeometry()
        if shape in {MaskShapeKind.POLYGON, MaskShapeKind.BEZIER}:
            geometry = MaskGeometry(
                points=(
                    MaskPoint(x=0.25, y=0.25),
                    MaskPoint(x=0.75, y=0.25),
                    MaskPoint(x=0.75, y=0.75),
                    MaskPoint(x=0.25, y=0.75),
                )
            )
        self._session.state.binding.require_timeline().add_clip_mask(
            clip.id,
            shape,
            name={
                MaskShapeKind.RECTANGLE: "矩形蒙版",
                MaskShapeKind.ELLIPSE: "椭圆蒙版",
                MaskShapeKind.POLYGON: "多边形蒙版",
                MaskShapeKind.BEZIER: "贝塞尔蒙版",
            }[shape],
            geometry=geometry,
        )
        self._after_change(clip.id)
        self._session._set_status("蒙版已添加")

    @Slot(str, str, bool, str, bool, int, int, float, float, float, float, float, float, str)
    @report_ui_errors
    def updateSelectedClipMask(
        self,
        mask_id: str,
        name: str,
        enabled: bool,
        combine_mode: str,
        inverted: bool,
        feather: int,
        feather_passes: int,
        opacity: float,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
        rotation: float,
        points_json: str,
    ) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        mask = next(item for item in clip.masks if item.id == mask_id)
        self._session.state.binding.require_timeline().update_clip_mask(
            clip.id,
            mask_id,
            name=name.strip(),
            enabled=enabled,
            combine_mode=combine_mode,
            inverted=inverted,
            feather=feather,
            feather_passes=feather_passes,
            opacity=opacity,
            geometry=self._geometry(
                mask.kind,
                center_x,
                center_y,
                width,
                height,
                rotation,
                points_json,
            ),
        )
        self._after_change(clip.id)
        self._session._set_status("蒙版已更新")

    @Slot(str, int)
    @report_ui_errors
    def moveSelectedClipMask(self, mask_id: str, position: int) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().move_clip_mask(
            clip.id, mask_id, position
        )
        self._after_change(clip.id)
        self._session._set_status("蒙版顺序已更新")

    @Slot(str)
    @report_ui_errors
    def removeSelectedClipMask(self, mask_id: str) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().remove_clip_mask(clip.id, mask_id)
        self._after_change(clip.id)
        self._session._set_status("蒙版已移除")

    @Slot(str, str)
    @report_ui_errors
    def assignSelectedEffectMask(self, effect_id: str, mask_id: str) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().assign_clip_visual_effect_mask(
            clip.id,
            effect_id,
            mask_id or None,
        )
        self._after_change(clip.id)
        self._session._set_status("效果蒙版已更新")

    @Slot(str, int, float, float, float, float, float, str, str, float, float, float, float)
    @report_ui_errors
    def setMaskKeyframe(
        self,
        mask_id: str,
        timeline_frame: int,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
        rotation: float,
        points_json: str,
        interpolation: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        mask = next(item for item in clip.masks if item.id == mask_id)
        self._session.state.binding.require_timeline().upsert_clip_mask_keyframe(
            clip.id,
            mask_id,
            self._local_offset(clip, timeline_frame),
            self._geometry(
                mask.kind,
                center_x,
                center_y,
                width,
                height,
                rotation,
                points_json,
            ),
            curve=KeyframeCurve(
                interpolation=cast(KeyframeInterpolation, interpolation),
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            ),
        )
        self._after_change(clip.id)
        self._session._set_status("蒙版关键帧已保存")

    @Slot(str, int)
    @report_ui_errors
    def removeMaskKeyframe(self, mask_id: str, timeline_offset: int) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().remove_clip_mask_keyframe(
            clip.id,
            mask_id,
            timeline_offset,
        )
        self._after_change(clip.id)
        self._session._set_status("蒙版关键帧已移除")

    @Slot(str, int, int)
    @report_ui_errors
    def moveMaskKeyframe(
        self,
        mask_id: str,
        old_timeline_offset: int,
        new_timeline_offset: int,
    ) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().move_clip_mask_keyframe(
            clip.id,
            mask_id,
            old_timeline_offset,
            new_timeline_offset,
        )
        self._after_change(clip.id)
        self._session._set_status("蒙版关键帧已移动")

    @Slot(str, list, int, float)
    @report_ui_errors
    def retimeMaskKeyframes(
        self,
        mask_id: str,
        timeline_offsets: list[int],
        anchor_offset: int,
        scale: float,
    ) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        self._session.state.binding.require_timeline().retime_clip_mask_keyframes(
            clip.id,
            mask_id,
            [int(value) for value in timeline_offsets],
            anchor_offset=anchor_offset,
            scale=scale,
        )
        self._after_change(clip.id)
        self._session._set_status("蒙版关键帧时间已缩放")

    @Slot(str)
    @report_ui_errors
    def trackSelectedMask(self, mask_id: str) -> None:
        self._session._require_writable()
        clip = self._selected_clip()
        next(item for item in clip.masks if item.id == mask_id)
        self._session.tasks.start(
            TrackMaskCommand(
                sequence_id=self._session.state.binding.active_sequence_id,
                clip_id=clip.id,
                mask_id=mask_id,
            ),
            [clip.asset_id],
            sequence_id=self._session.state.binding.active_sequence_id,
        )
        self._session._set_status("正在跟踪蒙版")

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
    def _geometry(
        kind: MaskShapeKind,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
        rotation: float,
        points_json: str,
    ) -> MaskGeometry:
        points: tuple[MaskPoint, ...] = ()
        if kind in {MaskShapeKind.POLYGON, MaskShapeKind.BEZIER}:
            try:
                raw_points = json.loads(points_json)
            except json.JSONDecodeError as error:
                raise ValueError("多边形点必须是有效 JSON") from error
            if not isinstance(raw_points, list):
                raise ValueError("多边形点必须是 JSON 数组")
            points = tuple(
                MaskPoint.model_validate(
                    {"x": float(item[0]), "y": float(item[1])}
                    if isinstance(item, list)
                    else item
                )
                for item in raw_points
            )
        return MaskGeometry(
            center_x=center_x,
            center_y=center_y,
            width=width,
            height=height,
            rotation=rotation,
            points=points,
        )

    @staticmethod
    def _geometry_row(geometry: MaskGeometry) -> dict[str, Any]:
        return {
            "centerX": geometry.center_x,
            "centerY": geometry.center_y,
            "width": geometry.width,
            "height": geometry.height,
            "rotation": geometry.rotation,
            "pointsJson": json.dumps(
                [item.model_dump(mode="json") for item in geometry.points],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }

    def _after_change(self, clip_id: str) -> None:
        clip = self._selected_clip()
        assert clip is not None and clip.id == clip_id
        self._session.projectors.timeline.refresh_clip_rows(
            [clip.id],
            clips=[clip],
            refresh_relations=False,
        )
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
