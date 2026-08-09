from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.domain.timebase import (
    source_frame_at_timeline_offset,
    timeline_offset_for_source_frame,
)

from .controller_facet import ControllerFacet, report_ui_errors
from .web_editor_context import (
    WebEditorContext,
    coerce_web_descriptor_value,
    find_web_descriptor,
    require_mutable_web_clip,
)

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.web_controller import WebController


class WebTimelineController(ControllerFacet):
    timelineStateChanged = Signal()
    browserRuntimePreviewRequested = Signal(str)

    def __init__(self, session, editor: WebController):
        super().__init__(session)
        self.setObjectName("webTimelineController")
        self._editor = editor
        editor.webStateChanged.connect(self.timelineStateChanged.emit)
        editor.webSelectionChanged.connect(self.timelineStateChanged.emit)

    @property
    def _context(self) -> WebEditorContext:
        return self._editor.context_snapshot()

    def _selected_layer_descriptor_rows(self) -> list[dict]:
        prefix = f"{self._context.selected_layer_id}."
        return [
            dict(item)
            for item in self._context.edit_document.get("fields", [])
            if item.get("target") == "layer" and str(item.get("source_id", "")).startswith(prefix)
        ]

    @Property("QVariantList", notify=timelineStateChanged)
    def timelineItemsData(self) -> list[dict]:
        scene_duration = int(self._context.edit_document.get("scene_duration_ms") or 0)
        descriptors = {str(item.get("source_id")): item for item in self._selected_layer_descriptor_rows()}
        items: list[dict] = []
        layer_id = self._context.selected_layer_id
        enter = descriptors.get(f"{layer_id}.enter_ms")
        exit_item = descriptors.get(f"{layer_id}.exit_ms")
        if enter is not None or exit_item is not None:
            exit_value = (exit_item or {}).get("value")
            items.append(
                {
                    "kind": "interval",
                    "id": f"{layer_id}.visibility",
                    "label": "显示区间",
                    "startField": "enter_ms",
                    "endField": "exit_ms",
                    "startMs": int((enter or {}).get("value") or 0),
                    "endMs": (int(exit_value) if isinstance(exit_value, (int, float)) else scene_duration),
                    "durationMs": scene_duration,
                }
            )
        delay = descriptors.get(f"{layer_id}.delay_ms")
        duration = descriptors.get(f"{layer_id}.duration_ms")
        if delay is not None or duration is not None:
            start_ms = int((delay or {}).get("value") or 0)
            items.append(
                {
                    "kind": "interval",
                    "id": f"{layer_id}.motion",
                    "label": "动画区间",
                    "startField": "delay_ms",
                    "endField": "duration_ms",
                    "endIsDuration": True,
                    "startMs": start_ms,
                    "endMs": min(
                        scene_duration,
                        start_ms + int((duration or {}).get("value") or 0),
                    ),
                    "durationMs": scene_duration,
                }
            )
        items.extend(
            {
                **item,
                "kind": "keyframe",
                "durationMs": scene_duration,
            }
            for item in self._keyframe_rows()
            if item.get("sceneId") == self._context.active_scene_id
            and (item.get("target") == "parameter" or item.get("layerId") == self._context.selected_layer_id)
        )
        return items

    @Property("QVariantList", notify=timelineStateChanged)
    def keyframesData(self) -> list[dict]:
        return self._keyframe_rows()

    def _keyframe_rows(self) -> list[dict]:
        clip = self._selected_clip()
        if clip is None or self._session.binding.timeline is None:
            return []
        profile = self._session.binding.timeline.state.sequence.profile
        fps = profile.fps
        scene_start_ms = self._scene_start_ms(self._context.active_scene_id)
        values: list[dict] = []
        scene = (self._context.persistent_state.get("scenes") or {}).get(self._context.active_scene_id, {})
        for layer_id, tracks in scene.get("animations", {}).items():
            for field, track in tracks.items():
                for keyframe in track.get("keyframes", []):
                    source_frame = round((scene_start_ms + int(keyframe["time_ms"])) * fps / 1000)
                    local_frame = timeline_offset_for_source_frame(
                        clip.source_in,
                        source_frame,
                        clip.speed_numerator,
                        clip.speed_denominator,
                        freeze_source_frame=clip.freeze_source_frame,
                    )
                    frame = clip.timeline_start + local_frame
                    if clip.timeline_start <= frame < clip.timeline_end:
                        values.append(
                            {
                                "target": "layer",
                                "sourceId": f"{layer_id}.{field}",
                                "path": (f"scenes.{self._context.active_scene_id}.layers.{layer_id}.{field}"),
                                "sceneId": self._context.active_scene_id,
                                "layerId": layer_id,
                                "field": field,
                                "timeMs": int(keyframe["time_ms"]),
                                "frame": frame,
                                "value": keyframe.get("value"),
                                "valueText": json.dumps(keyframe.get("value"), ensure_ascii=False),
                                "easing": (keyframe.get("easing") or {}).get("kind", "linear"),
                            }
                        )
        for parameter_id, track in scene.get("parameter_animations", {}).items():
            for keyframe in track.get("keyframes", []):
                source_frame = round((scene_start_ms + int(keyframe["time_ms"])) * fps / 1000)
                local_frame = timeline_offset_for_source_frame(
                    clip.source_in,
                    source_frame,
                    clip.speed_numerator,
                    clip.speed_denominator,
                    freeze_source_frame=clip.freeze_source_frame,
                )
                frame = clip.timeline_start + local_frame
                if clip.timeline_start <= frame < clip.timeline_end:
                    values.append(
                        {
                            "target": "parameter",
                            "sourceId": parameter_id,
                            "path": (
                                f"scenes.{self._context.active_scene_id}.parameter_animations.{parameter_id}"
                            ),
                            "sceneId": self._context.active_scene_id,
                            "layerId": "",
                            "field": parameter_id,
                            "timeMs": int(keyframe["time_ms"]),
                            "frame": frame,
                            "value": keyframe.get("value"),
                            "valueText": json.dumps(
                                keyframe.get("value"),
                                ensure_ascii=False,
                            ),
                            "easing": (keyframe.get("easing") or {}).get("kind", "linear"),
                        }
                    )
        return sorted(values, key=lambda item: (item["frame"], item["layerId"], item["field"]))

    @Slot(str, str, "QVariant", str, int)
    @report_ui_errors
    def setDescriptorKeyframeAtFrame(
        self,
        target: str,
        source_id: str,
        value,
        easing: str,
        frame: int,
    ) -> None:
        current = require_mutable_web_clip(self._session, self._context.clip_id)
        descriptor = find_web_descriptor(
            self._context.edit_document,
            target,
            source_id,
        )
        typed_value = coerce_web_descriptor_value(descriptor, value)
        scene_id, time_ms = self._scene_time_for_frame(frame)
        revision = int(self._context.persistent_state.get("revision", 0))
        if target == "layer":
            layer_id, field = source_id.rsplit(".", 1)
            updated = current.set_web_keyframe(
                self._session.binding.active_sequence_id,
                self._context.clip_id,
                layer_id,
                field,
                time_ms,
                typed_value,
                scene_id=scene_id,
                easing={"kind": easing or "linear"},
                expected_revision=revision,
                actor="human",
            )
        elif target == "parameter":
            updated = current.set_web_parameter_keyframe(
                self._session.binding.active_sequence_id,
                self._context.clip_id,
                source_id,
                time_ms,
                typed_value,
                scene_id=scene_id,
                easing={"kind": easing or "linear"},
                expected_revision=revision,
                actor="human",
            )
        else:
            raise ValueError("当前字段不能创建关键帧")
        self._accept_state(updated)

    @Slot(str, str, int)
    @report_ui_errors
    def removeDescriptorKeyframeAtFrame(
        self,
        target: str,
        source_id: str,
        frame: int,
    ) -> None:
        current = require_mutable_web_clip(self._session, self._context.clip_id)
        scene_id, time_ms = self._scene_time_for_frame(frame)
        revision = int(self._context.persistent_state.get("revision", 0))
        if target == "layer":
            layer_id, field = source_id.rsplit(".", 1)
            updated = current.remove_web_keyframe(
                self._session.binding.active_sequence_id,
                self._context.clip_id,
                layer_id,
                field,
                time_ms,
                scene_id=scene_id,
                expected_revision=revision,
            )
        elif target == "parameter":
            updated = current.remove_web_parameter_keyframe(
                self._session.binding.active_sequence_id,
                self._context.clip_id,
                source_id,
                time_ms,
                scene_id=scene_id,
                expected_revision=revision,
            )
        else:
            raise ValueError("当前字段没有关键帧")
        self._accept_state(updated)

    @Slot(str, str, int, int)
    @report_ui_errors
    def moveTimelineKeyframe(
        self,
        target: str,
        source_id: str,
        old_time_ms: int,
        frame: int,
    ) -> None:
        current = require_mutable_web_clip(self._session, self._context.clip_id)
        scene_id, new_time_ms = self._scene_time_for_frame(frame)
        if scene_id != self._context.active_scene_id:
            raise ValueError("关键帧不能跨场景拖动")
        revision = int(self._context.persistent_state.get("revision", 0))
        if target == "layer":
            layer_id, field = source_id.rsplit(".", 1)
            updated = current.move_web_keyframe(
                self._session.binding.active_sequence_id,
                self._context.clip_id,
                layer_id,
                field,
                old_time_ms,
                new_time_ms,
                scene_id=scene_id,
                expected_revision=revision,
            )
        elif target == "parameter":
            updated = current.move_web_parameter_keyframe(
                self._session.binding.active_sequence_id,
                self._context.clip_id,
                source_id,
                old_time_ms,
                new_time_ms,
                scene_id=scene_id,
                expected_revision=revision,
            )
        else:
            raise ValueError("当前字段没有可移动的关键帧")
        self._accept_state(updated)

    @Slot(str, str, int, int)
    def previewTimelineKeyframe(
        self,
        target: str,
        source_id: str,
        old_time_ms: int,
        frame: int,
    ) -> None:
        try:
            scene_id, new_time_ms = self._scene_time_for_frame(frame)
            if scene_id != self._context.active_scene_id:
                return
            preview = json.loads(json.dumps(self._context.runtime_state))
            scene = preview["scenes"][scene_id]
            if target == "layer":
                layer_id, field = source_id.rsplit(".", 1)
                track = scene["animations"][layer_id][field]
            else:
                track = scene["parameter_animations"][source_id]
            moving = next(item for item in track["keyframes"] if int(item["time_ms"]) == int(old_time_ms))
            moving["time_ms"] = new_time_ms
            track["keyframes"].sort(key=lambda item: int(item["time_ms"]))
            self.browserRuntimePreviewRequested.emit(
                json.dumps(
                    preview,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        except (KeyError, StopIteration, TypeError, ValueError):
            return

    @Slot(str, str, int, int, bool)
    def previewTimelineInterval(
        self,
        start_field: str,
        end_field: str,
        start_ms: int,
        end_ms: int,
        end_is_duration: bool,
    ) -> None:
        if not self._context.selected_layer_id:
            return
        preview = json.loads(json.dumps(self._context.runtime_state))
        scene = preview.get("scenes", {}).get(self._context.active_scene_id, {})
        layer = scene.get("layers", {}).get(self._context.selected_layer_id)
        if not isinstance(layer, dict):
            return
        layer[start_field] = max(0, int(start_ms))
        layer[end_field] = max(0, int(end_ms) - int(start_ms)) if end_is_duration else max(0, int(end_ms))
        self.browserRuntimePreviewRequested.emit(
            json.dumps(preview, ensure_ascii=False, separators=(",", ":"))
        )

    @Slot(str, str, int, int, bool)
    @report_ui_errors
    def commitTimelineInterval(
        self,
        start_field: str,
        end_field: str,
        start_ms: int,
        end_ms: int,
        end_is_duration: bool,
    ) -> None:
        if not self._context.selected_layer_id:
            raise ValueError("请先选择网页图层")
        value = max(0, int(end_ms) - int(start_ms)) if end_is_duration else max(0, int(end_ms))
        current = require_mutable_web_clip(self._session, self._context.clip_id)
        updated = current.update_web_clip(
            self._session.binding.active_sequence_id,
            self._context.clip_id,
            {
                self._context.selected_layer_id: {
                    start_field: max(0, int(start_ms)),
                    end_field: value,
                }
            },
            scene_id=self._context.active_scene_id,
            expected_revision=int(self._context.persistent_state.get("revision", 0)),
            actor="human",
        )
        self._accept_state(updated)

    @Slot()
    def cancelTimelinePreview(self) -> None:
        self.browserRuntimePreviewRequested.emit(self.stateJson)

    @Slot(int, result=int)
    def timeMsForFrame(self, frame: int) -> int:
        try:
            return self._time_ms_for_frame(frame)
        except RuntimeError:
            return 0

    @Slot(int, result=int)
    def sceneTimeMsForFrame(self, frame: int) -> int:
        try:
            scene_id, time_ms = self._scene_time_for_frame(frame)
        except RuntimeError:
            return 0
        return time_ms if scene_id == self._context.active_scene_id else 0

    @Slot(int, result=int)
    def frameForSceneTime(self, time_ms: int) -> int:
        clip = self._selected_clip()
        if clip is None or self._session.binding.timeline is None:
            return 0
        profile = self._session.binding.timeline.state.sequence.profile
        global_time_ms = self._scene_start_ms(self._context.active_scene_id) + max(0, int(time_ms))
        source_frame = round(global_time_ms * profile.fps / 1000)
        local_frame = timeline_offset_for_source_frame(
            clip.source_in,
            source_frame,
            clip.speed_numerator,
            clip.speed_denominator,
            freeze_source_frame=clip.freeze_source_frame,
        )
        return max(
            clip.timeline_start,
            min(clip.timeline_end - 1, clip.timeline_start + local_frame),
        )

    @Slot(int, result=int)
    def snapSceneTimeMs(self, time_ms: int) -> int:
        duration = int(self._context.edit_document.get("scene_duration_ms") or 1)
        bounded = max(0, min(duration - 1, int(time_ms)))
        if self._session.binding.timeline is None:
            return bounded
        fps = self._session.binding.timeline.state.sequence.profile.fps
        frame_ms = 1000 / fps
        snapped = round(bounded / frame_ms) * frame_ms
        scene: dict = next(
            (
                item
                for item in self._context.manifest.get("scenes", [])
                if item.get("id") == self._context.active_scene_id
            ),
            {},
        )
        semantic_times = [int(item.get("at_ms") or 0) for item in scene.get("steps", [])]
        if semantic_times:
            nearest = min(
                semantic_times,
                key=lambda value: abs(value - snapped),
            )
            if abs(nearest - snapped) <= max(80, frame_ms * 3):
                snapped = nearest
        return max(0, min(duration - 1, round(snapped)))

    @Slot(int)
    def setActiveFrame(self, frame: int) -> None:
        try:
            scene_id, _local_time_ms = self._scene_time_for_frame(frame)
        except RuntimeError:
            return
        self._editor.activate_scene(scene_id)

    def _selected_clip(self):
        if self._session.binding.timeline is None or not self._context.clip_id:
            return None
        return next(
            (item for item in self._session.binding.timeline.state.clips if item.id == self._context.clip_id),
            None,
        )

    def _scene_start_ms(self, scene_id: str) -> int:
        elapsed = 0
        for scene in self._context.manifest.get("scenes", []):
            if scene.get("id") == scene_id:
                return elapsed
            elapsed += int(scene.get("duration_ms") or 0)
        return 0

    def _time_ms_for_frame(self, frame: int) -> int:
        clip = self._selected_clip()
        if clip is None or self._session.binding.timeline is None:
            raise RuntimeError("No editable web clip is selected")
        profile = self._session.binding.timeline.state.sequence.profile
        local_frame = max(0, min(clip.duration - 1, int(frame) - clip.timeline_start))
        source_frame = source_frame_at_timeline_offset(
            clip.source_in,
            local_frame,
            clip.speed_numerator,
            clip.speed_denominator,
            freeze_source_frame=clip.freeze_source_frame,
        )
        return max(0, round(source_frame * 1000 / profile.fps))

    def _scene_time_for_frame(self, frame: int) -> tuple[str, int]:
        global_time_ms = self._time_ms_for_frame(frame)
        elapsed = 0
        scenes = self._context.manifest.get("scenes", [])
        for scene in scenes:
            duration = int(scene.get("duration_ms") or 0)
            if global_time_ms < elapsed + duration:
                return str(scene["id"]), max(0, global_time_ms - elapsed)
            elapsed += duration
        if not scenes:
            raise RuntimeError("Editable media manifest has no scenes")
        last = scenes[-1]
        return str(last["id"]), max(0, int(last.get("duration_ms") or 1) - 1)

    def _accept_state(self, _state) -> None:
        self._session.events.historyChanged.emit()
        self._session.projectors.timeline.schedule_preview_graph()
