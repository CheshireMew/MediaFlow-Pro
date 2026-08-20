from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.application.web_clip_editing_context import web_clip_editing_context
from mediaflow.domain.web_manifest_primitives import WebInterpolation
from mediaflow.domain.web_state import (
    WebClipState,
    WebEasing,
    WebKeyframe,
    WebParameterAnimationTrack,
    WebSceneState,
)


class WebClipParameterEditing:
    def update_parameter(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        value: JsonValue,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
    ) -> WebClipState:
        editor, _asset, spec, current = web_clip_editing_context(self)._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        definition = spec.manifest.parameter_for(parameter_id)
        definition.descriptor.validate_value(value)
        if definition.binding.scope == "global":
            if actor == "automation" and parameter_id in current.parameter_locks:
                raise PermissionError(f"Editable parameter is locked: {parameter_id}")
            parameters = dict(current.parameters)
            if value == definition.descriptor.default:
                parameters.pop(parameter_id, None)
            else:
                parameters[parameter_id] = cast(str | float | int | bool, value)
            candidate = current.model_copy(update={"parameters": parameters})
        else:
            resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
            current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
            if actor == "automation" and parameter_id in current_scene.parameter_locks:
                raise PermissionError(
                    f"Editable scene parameter is locked: {resolved_scene_id}/{parameter_id}"
                )
            scene_definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
            base = scene_definition.parameters.get(parameter_id, definition.descriptor.default)
            parameters = dict(current_scene.parameters)
            if value == base:
                parameters.pop(parameter_id, None)
            else:
                parameters[parameter_id] = cast(str | float | int | bool, value)
            scenes = dict(current.scenes)
            scenes[resolved_scene_id] = current_scene.model_copy(update={"parameters": parameters})
            candidate = current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id})
        return web_clip_editing_context(self)._save_state(editor, current, candidate)

    def set_parameter_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        time_ms: int,
        value: JsonValue,
        *,
        scene_id: str | None = None,
        easing: Mapping[str, object] | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
    ) -> WebClipState:
        editor, _asset, spec, current = web_clip_editing_context(self)._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        definition = spec.manifest.parameter_for(parameter_id)
        if definition.descriptor.timeline != "keyframe":
            raise ValueError(f"Editable parameter is not animatable: {parameter_id}")
        definition.descriptor.validate_value(value)
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        scene_definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
        if time_ms >= scene_definition.duration_ms:
            raise ValueError("Editable parameter keyframe must stay inside its scene")
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        locked = (
            parameter_id in current.parameter_locks
            if definition.binding.scope == "global"
            else parameter_id in current_scene.parameter_locks
        )
        if actor == "automation" and locked:
            raise PermissionError(f"Editable parameter is locked: {parameter_id}")
        animations = dict(current_scene.parameter_animations)
        existing = animations.get(parameter_id)
        keyframes = [item for item in (existing.keyframes if existing else []) if item.time_ms != time_ms]
        keyframes.append(
            WebKeyframe(
                time_ms=time_ms,
                value=value,
                easing=WebEasing.model_validate(easing or {}),
            )
        )
        keyframes.sort(key=lambda item: item.time_ms)
        interpolation: WebInterpolation = (
            "continuous" if definition.descriptor.kind in {"number", "integer"} else "discrete"
        )
        animations[parameter_id] = WebParameterAnimationTrack(
            parameter_id=parameter_id,
            interpolation=interpolation,
            keyframes=keyframes,
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"parameter_animations": animations})
        return web_clip_editing_context(self)._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def remove_parameter_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        time_ms: int,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = web_clip_editing_context(self)._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        spec.manifest.parameter_for(parameter_id)
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        animations = dict(current_scene.parameter_animations)
        track = animations.get(parameter_id)
        if track is None:
            raise KeyError(f"{parameter_id}/{time_ms}")
        remaining = [item for item in track.keyframes if item.time_ms != time_ms]
        if len(remaining) == len(track.keyframes):
            raise KeyError(f"{parameter_id}/{time_ms}")
        if remaining:
            animations[parameter_id] = track.model_copy(update={"keyframes": remaining})
        else:
            animations.pop(parameter_id)
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"parameter_animations": animations})
        return web_clip_editing_context(self)._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def move_parameter_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        old_time_ms: int,
        new_time_ms: int,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = web_clip_editing_context(self)._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        spec.manifest.parameter_for(parameter_id)
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
        if new_time_ms < 0 or new_time_ms >= definition.duration_ms:
            raise ValueError("Editable parameter keyframe must stay inside its scene")
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        animations = dict(current_scene.parameter_animations)
        track = animations.get(parameter_id)
        if track is None:
            raise KeyError(f"{parameter_id}/{old_time_ms}")
        moving = next(
            (item for item in track.keyframes if item.time_ms == old_time_ms),
            None,
        )
        if moving is None:
            raise KeyError(f"{parameter_id}/{old_time_ms}")
        if any(item.time_ms == new_time_ms and item.time_ms != old_time_ms for item in track.keyframes):
            raise ValueError("Editable parameter keyframe destination is occupied")
        keyframes = [item for item in track.keyframes if item.time_ms != old_time_ms]
        keyframes.append(moving.model_copy(update={"time_ms": new_time_ms}))
        keyframes.sort(key=lambda item: item.time_ms)
        animations[parameter_id] = track.model_copy(update={"keyframes": keyframes})
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"parameter_animations": animations})
        return web_clip_editing_context(self)._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def set_parameter_lock(
        self,
        sequence_id: str,
        clip_id: str,
        parameter_id: str,
        locked: bool,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = web_clip_editing_context(self)._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        definition = spec.manifest.parameter_for(parameter_id)
        if definition.binding.scope == "global":
            locks = set(current.parameter_locks)
            if locked:
                locks.add(parameter_id)
            else:
                locks.discard(parameter_id)
            candidate = current.model_copy(
                update={
                    "parameter_locks": tuple(
                        item.descriptor.id
                        for item in spec.manifest.parameters
                        if item.binding.scope == "global" and item.descriptor.id in locks
                    )
                }
            )
        else:
            resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
            current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
            locks = set(current_scene.parameter_locks)
            if locked:
                locks.add(parameter_id)
            else:
                locks.discard(parameter_id)
            scenes = dict(current.scenes)
            scenes[resolved_scene_id] = current_scene.model_copy(
                update={
                    "parameter_locks": tuple(
                        item.descriptor.id
                        for item in spec.manifest.parameters
                        if item.binding.scope == "scene" and item.descriptor.id in locks
                    )
                }
            )
            candidate = current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id})
        return web_clip_editing_context(self)._save_state(editor, current, candidate)
