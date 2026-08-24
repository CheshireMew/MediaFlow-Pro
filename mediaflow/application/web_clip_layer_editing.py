from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.application.web_clip_editing_context import web_clip_editing_context
from mediaflow.application.web_field_validation import WebFieldValidator
from mediaflow.application.web_keyframe_operations import (
    move_web_keyframe,
    remove_web_keyframe,
    upsert_web_keyframe,
)
from mediaflow.domain.web_manifest_primitives import (
    CONTINUOUS_ANIMATION_FIELDS,
    WebEditableField,
    WebInterpolation,
)
from mediaflow.domain.web_state import (
    WebAnimationTrack,
    WebClipState,
    WebLayerOverride,
    WebSceneState,
    WebStateDiff,
    web_runtime_state,
)


class WebClipLayerEditing:
    def update_clip(
        self,
        sequence_id: str,
        clip_id: str,
        updates: Mapping[str, Mapping[str, object]],
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "human",
    ) -> WebClipState:
        editor, asset, spec, current = web_clip_editing_context(self)._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        layers = dict(current_scene.layers)
        manifest_layers = {layer.id: layer for layer in spec.manifest.layers}
        package_root = web_files.web_package_root(
            web_clip_editing_context(self).repository.assets.resolve_asset_path(asset),
            spec.manifest,
        )
        media_sources = web_clip_editing_context(self)._media_sources(
            package_root,
            spec.manifest,
        )
        media_source_ids = {item.id for item in media_sources.sources}
        for layer_id, patch in updates.items():
            manifest_layer = manifest_layers.get(layer_id)
            if manifest_layer is None:
                raise ValueError(f"Editable media layer is not declared: {layer_id}")
            patch_fields = set(patch)
            unknown = patch_fields - set(WebLayerOverride.model_fields)
            if unknown:
                raise ValueError(f"Unknown editable media fields: {sorted(unknown)}")
            disallowed = patch_fields - set(manifest_layer.editable)
            if disallowed:
                raise ValueError(f"Layer {layer_id} does not allow fields: {sorted(disallowed)}")
            locked = patch_fields & set(current_scene.locks.get(layer_id, ()))
            if actor == "automation" and locked:
                raise PermissionError(f"Editable media fields are locked: {layer_id}/{sorted(locked)}")
            values = layers.get(layer_id, WebLayerOverride()).model_dump()
            values.update(patch)
            candidate = WebLayerOverride.model_validate(values)
            if candidate.image is not None and candidate.image not in media_source_ids:
                raise ValueError(f"Layer {layer_id} image is not declared in the v4 media-sources manifest")
            for field in patch:
                WebFieldValidator.constraint(
                    layer_id,
                    field,
                    getattr(candidate, field),
                    manifest_layer.constraints.get(cast(WebEditableField, field)),
                )
            if candidate.changed_fields():
                layers[layer_id] = candidate
            else:
                layers.pop(layer_id, None)

        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"layers": layers})
        updated = current.model_copy(
            update={
                "scenes": scenes,
                "scene_id": resolved_scene_id,
                "revision": current.revision + 1,
            }
        )
        web_contract.validate_media_bindings(
            spec.manifest,
            media_sources,
            updated,
        )
        editor.set_web_clip_state(updated, expected_revision=current.revision)
        return editor.state.web_states[clip_id]

    def diff_clip_update(
        self,
        sequence_id: str,
        clip_id: str,
        updates: Mapping[str, Mapping[str, object]],
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        actor: Literal["human", "automation"] = "automation",
    ) -> WebStateDiff:
        _editor, _asset, spec, current = web_clip_editing_context(self)._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        resolved = web_runtime_state(current, spec.manifest)
        resolved_scenes = resolved.get("scenes")
        runtime_scene = resolved_scenes.get(resolved_scene_id) if isinstance(resolved_scenes, dict) else None
        resolved_layers = runtime_scene.get("layers") if isinstance(runtime_scene, dict) else None
        if not isinstance(resolved_layers, dict):
            resolved_layers = {}
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())

        def current_value(layer_id: str, field: str) -> JsonValue:
            layer_values = resolved_layers.get(layer_id)
            if not isinstance(layer_values, dict):
                return None
            return layer_values.get(field)

        locked_paths = [
            f"scenes.{resolved_scene_id}.layers.{layer_id}.{field}"
            for layer_id, patch in updates.items()
            for field in patch
            if actor == "automation" and field in current_scene.locks.get(layer_id, ())
        ]
        return WebStateDiff(
            clip_id=clip_id,
            before_revision=current.revision,
            changes={
                f"scenes.{resolved_scene_id}.layers.{layer_id}.{field}": {
                    "before": current_value(layer_id, field),
                    "after": cast(JsonValue, value),
                }
                for layer_id, patch in updates.items()
                for field, value in patch.items()
                if current_value(layer_id, field) != value
            },
            locked_paths=locked_paths,
        )

    def set_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        field: str,
        time_ms: int,
        value: object,
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
        layer = next((item for item in spec.manifest.layers if item.id == layer_id), None)
        if layer is None:
            raise ValueError(f"Editable media layer is not declared: {layer_id}")
        if field not in layer.editable:
            raise ValueError(f"Layer {layer_id} does not allow field: {field}")
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        scene_definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
        if time_ms >= scene_definition.duration_ms:
            raise ValueError("Editable media keyframe must stay inside its scene")
        if actor == "automation" and field in current_scene.locks.get(layer_id, ()):
            raise PermissionError(f"Editable media field is locked: {layer_id}/{field}")
        validated_value = WebFieldValidator.layer_value(layer_id, field, value, layer.constraints.get(field))
        animations = {key: dict(tracks) for key, tracks in current_scene.animations.items()}
        layer_tracks = animations.setdefault(layer_id, {})
        existing = layer_tracks.get(field)
        keyframes = upsert_web_keyframe(
            existing.keyframes if existing else (),
            time_ms=time_ms,
            value=validated_value,
            easing=easing,
        )
        interpolation = "continuous" if field in CONTINUOUS_ANIMATION_FIELDS else "discrete"
        layer_tracks[field] = WebAnimationTrack(
            field=cast(WebEditableField, field),
            interpolation=cast(WebInterpolation, interpolation),
            keyframes=keyframes,
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"animations": animations})
        return web_clip_editing_context(self)._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def remove_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        field: str,
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
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        scene_definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
        if time_ms >= scene_definition.duration_ms:
            raise ValueError("Editable media keyframe must stay inside its scene")
        animations = {key: dict(tracks) for key, tracks in current_scene.animations.items()}
        tracks = animations.get(layer_id)
        if not tracks or field not in tracks:
            raise KeyError(f"{layer_id}/{field}/{time_ms}")
        remaining = remove_web_keyframe(
            tracks[field].keyframes,
            time_ms=time_ms,
            missing_identity=f"{layer_id}/{field}/{time_ms}",
        )
        if remaining:
            tracks[field] = tracks[field].model_copy(update={"keyframes": remaining})
        else:
            tracks.pop(field)
        if not tracks:
            animations.pop(layer_id, None)
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"animations": animations})
        return web_clip_editing_context(self)._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def move_keyframe(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        field: str,
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
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
        if new_time_ms < 0 or new_time_ms >= definition.duration_ms:
            raise ValueError("Editable media keyframe must stay inside its scene")
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        animations = {key: dict(tracks) for key, tracks in current_scene.animations.items()}
        track = animations.get(layer_id, {}).get(cast(WebEditableField, field))
        if track is None:
            raise KeyError(f"{layer_id}/{field}/{old_time_ms}")
        keyframes = move_web_keyframe(
            track.keyframes,
            old_time_ms=old_time_ms,
            new_time_ms=new_time_ms,
            missing_identity=f"{layer_id}/{field}/{old_time_ms}",
            occupied_message="Editable media keyframe destination is occupied",
        )
        animations[layer_id][cast(WebEditableField, field)] = track.model_copy(
            update={"keyframes": keyframes}
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"animations": animations})
        return web_clip_editing_context(self)._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def set_field_locks(
        self,
        sequence_id: str,
        clip_id: str,
        layer_id: str,
        fields: list[str],
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
        manifest_layer = next((item for item in spec.manifest.layers if item.id == layer_id), None)
        if manifest_layer is None:
            raise ValueError(f"Editable media layer is not declared: {layer_id}")
        unknown = set(fields) - set(manifest_layer.editable)
        if unknown:
            raise ValueError(f"Cannot lock undeclared fields: {sorted(unknown)}")
        resolved_scene_id = web_clip_editing_context(self)._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        locks = {key: set(value) for key, value in current_scene.locks.items()}
        target = locks.setdefault(layer_id, set())
        if locked:
            target.update(cast(list[WebEditableField], fields))
        else:
            target.difference_update(fields)
        if not target:
            locks.pop(layer_id, None)
        normalized = {
            key: tuple(field for field in manifest_layer.editable if field in value)
            if key == layer_id
            else tuple(sorted(value))
            for key, value in locks.items()
        }
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"locks": normalized})
        return web_clip_editing_context(self)._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )
