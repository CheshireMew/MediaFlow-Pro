from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.application.ports import WebApplicationDocuments
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_edit_document_builder import build_web_edit_document
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.web_media import (
    CONTINUOUS_ANIMATION_FIELDS,
    EditableMediaManifest,
    WebAnimationTrack,
    WebClipState,
    WebDataField,
    WebDataSnapshot,
    WebEasing,
    WebEditableField,
    WebEditDocument,
    WebFieldConstraint,
    WebInterpolation,
    WebKeyframe,
    WebLayerOverride,
    WebParameterAnimationTrack,
    WebRuntimePlayback,
    WebRuntimeVariant,
    WebSceneState,
    WebStateDiff,
    web_runtime_state,
)


class WebClipEditingService:
    """Owns validated state changes for one editable-media clip."""

    def __init__(
        self,
        repository: WebApplicationDocuments,
        timeline: Callable[[str], TimelineEditor],
        packages: WebPackageService,
    ) -> None:
        self.repository = repository
        self._timeline = timeline
        self._packages = packages

    def get_clip(self, clip_id: str) -> WebClipState:
        return self.repository.web.get_web_clip_state(clip_id)

    def describe_clip_editing(
        self,
        sequence_id: str,
        clip_id: str,
        *,
        scene_id: str | None = None,
    ) -> WebEditDocument:
        _editor, _asset, spec, state = self._clip_context(
            sequence_id,
            clip_id,
            None,
        )
        manifest = spec.manifest
        resolved_scene_id = self._scene_id(state, manifest, scene_id)
        return build_web_edit_document(
            clip_id=clip_id,
            manifest=manifest,
            state=state,
            scene_id=resolved_scene_id,
        )

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
        editor, _asset, spec, current = self._clip_context(
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
            resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
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
        return self._save_state(editor, current, candidate)

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
        editor, asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        layers = dict(current_scene.layers)
        manifest_layers = {layer.id: layer for layer in spec.manifest.layers}
        package_root = web_files.web_package_root(
            self.repository.catalog.resolve_asset_path(asset),
            spec.manifest,
        )
        media_sources = web_contract.read_media_sources(
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
                self.validate_constraint(
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
        _editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
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

    def select_variant(
        self,
        sequence_id: str,
        clip_id: str,
        variant_id: str,
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        variant = spec.manifest.variant_for(variant_id)
        return self._save_state(
            editor,
            current,
            current.model_copy(
                update={
                    "variant": WebRuntimeVariant(
                        id=variant.id,
                        width=variant.canvas.width,
                        height=variant.canvas.height,
                    )
                }
            ),
        )

    def commit_runtime_state(
        self,
        sequence_id: str,
        clip_id: str,
        runtime_state: Mapping[str, object],
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        expected_keys = {
            "scenes",
            "theme",
            "theme_bindings",
            "parameters",
            "parameter_bindings",
            "parameter_locks",
            "variant",
            "scene_id",
            "playback",
            "revision",
        }
        if set(runtime_state) != expected_keys:
            raise ValueError("Editable media runtime state must use the complete v6 state contract")
        runtime_revision = runtime_state["revision"]
        if (
            not isinstance(runtime_revision, (int, float))
            or isinstance(runtime_revision, bool)
            or int(runtime_revision) != current.revision
        ):
            raise RuntimeError("Editable media browser revision does not match the persisted clip revision")

        variant_value = runtime_state["variant"]
        if not isinstance(variant_value, Mapping):
            raise ValueError("Editable media runtime variant must be an object")
        variant = spec.manifest.variant_for(str(variant_value.get("id") or ""))
        if (
            int(variant_value.get("width") or 0) != variant.canvas.width
            or int(variant_value.get("height") or 0) != variant.canvas.height
        ):
            raise ValueError("Editable media runtime variant dimensions do not match the manifest")

        scene_id = str(runtime_state["scene_id"])
        scene_definitions = {item.id: item for item in spec.manifest.scenes}
        if scene_id not in scene_definitions:
            raise ValueError(f"Editable media runtime scene is not declared: {scene_id}")
        scenes_value = runtime_state["scenes"]
        if not isinstance(scenes_value, Mapping) or set(scenes_value) != set(scene_definitions):
            raise ValueError("Editable media runtime must return every declared scene")

        theme_bindings = {item.id: item.css_variable for item in spec.manifest.theme_variables}
        if runtime_state["theme_bindings"] != theme_bindings:
            raise ValueError("Editable media runtime theme bindings do not match the manifest")
        theme_value = runtime_state["theme"]
        if not isinstance(theme_value, Mapping):
            raise ValueError("Editable media runtime theme must be an object")
        theme_defaults = {item.id: item.default for item in spec.manifest.theme_variables}
        if set(theme_value) != set(theme_defaults):
            raise ValueError("Editable media runtime theme must contain every declared variable")
        theme: dict[str, str | float] = {}
        variables = {item.id: item for item in spec.manifest.theme_variables}
        for variable_id, value in theme_value.items():
            variable = variables[str(variable_id)]
            if variable.kind == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"Theme variable {variable_id} must be numeric")
            elif not isinstance(value, str):
                raise ValueError(f"Theme variable {variable_id} must be text")
            self.validate_constraint(
                str(variable_id),
                "theme",
                value,
                variable.constraints,
            )
            if value != theme_defaults[variable_id]:
                theme[str(variable_id)] = cast(str | float, value)

        parameter_bindings = {
            item.descriptor.id: item.binding.css_variable
            for item in spec.manifest.parameters
            if item.binding.css_variable is not None
        }
        if runtime_state["parameter_bindings"] != parameter_bindings:
            raise ValueError("Editable media runtime parameter bindings do not match the manifest")
        parameters_value = runtime_state["parameters"]
        global_definitions = {
            item.descriptor.id: item
            for item in spec.manifest.parameters
            if item.binding.scope == "global"
        }
        if not isinstance(parameters_value, Mapping) or set(parameters_value) != set(global_definitions):
            raise ValueError("Editable media runtime parameters must contain every global parameter")
        parameters: dict[str, str | float | int | bool] = {}
        for parameter_id, value in parameters_value.items():
            definition = global_definitions[str(parameter_id)]
            definition.descriptor.validate_value(cast(JsonValue, value))
            if value != definition.descriptor.default:
                parameters[str(parameter_id)] = cast(
                    str | float | int | bool,
                    value,
                )
        parameter_locks_value = runtime_state["parameter_locks"]
        if (
            not isinstance(parameter_locks_value, list)
            or any(not isinstance(value, str) for value in parameter_locks_value)
            or not set(parameter_locks_value).issubset(global_definitions)
        ):
            raise ValueError("Editable media runtime global parameter locks are invalid")
        parameter_locks = tuple(
            parameter_id for parameter_id in global_definitions if parameter_id in set(parameter_locks_value)
        )

        playback_value = runtime_state["playback"]
        if not isinstance(playback_value, Mapping):
            raise ValueError("Editable media runtime playback must be an object")
        playback = WebRuntimePlayback.model_validate(dict(playback_value))
        media_source_ids = web_contract.media_source_ids(
            web_files.web_package_root(
                self.repository.catalog.resolve_asset_path(asset),
                spec.manifest,
            ),
            spec.manifest,
        )
        manifest_layers = {item.id: item for item in spec.manifest.layers}
        data_fields = {item.id: item for item in spec.manifest.data_fields}
        data_defaults = {item.id: item.default for item in spec.manifest.data_fields}
        scenes: dict[str, WebSceneState] = {}
        for current_scene_id, definition in scene_definitions.items():
            scene_value = scenes_value[current_scene_id]
            if not isinstance(scene_value, Mapping) or set(scene_value) != {
                "layers",
                "animations",
                "parameters",
                "parameter_animations",
                "parameter_locks",
                "data",
                "locks",
            }:
                raise ValueError(f"Editable media runtime scene {current_scene_id} is incomplete")
            layers_value = scene_value["layers"]
            if not isinstance(layers_value, Mapping) or set(layers_value) != set(manifest_layers):
                raise ValueError(f"Editable media runtime scene {current_scene_id} must contain every layer")
            layers: dict[str, WebLayerOverride] = {}
            for layer_id, layer_definition in manifest_layers.items():
                layer_value = layers_value[layer_id]
                if not isinstance(layer_value, Mapping):
                    raise ValueError(
                        f"Editable media runtime layer {current_scene_id}/{layer_id} must be an object"
                    )
                unknown = set(layer_value) - set(WebLayerOverride.model_fields)
                if unknown:
                    raise ValueError(
                        f"Editable media runtime layer {current_scene_id}/{layer_id} "
                        f"contains unknown fields: {sorted(unknown)}"
                    )
                base = spec.manifest.layer_values_for(variant.id, layer_id)
                overrides = {
                    str(field): value
                    for field, value in layer_value.items()
                    if field not in base or value != base[field]
                }
                disallowed = set(overrides) - set(layer_definition.editable)
                if disallowed:
                    raise ValueError(
                        f"Editable media runtime layer {current_scene_id}/{layer_id} "
                        f"changes non-editable fields: {sorted(disallowed)}"
                    )
                candidate = WebLayerOverride.model_validate(overrides)
                if candidate.image is not None and candidate.image not in media_source_ids:
                    raise ValueError(
                        f"Layer {layer_id} image is not declared in the v4 media-sources manifest"
                    )
                for field in overrides:
                    self.validate_constraint(
                        layer_id,
                        field,
                        getattr(candidate, field),
                        layer_definition.constraints.get(cast(WebEditableField, field)),
                    )
                if candidate.changed_fields():
                    layers[layer_id] = candidate

            animations_value = scene_value["animations"]
            if not isinstance(animations_value, Mapping):
                raise ValueError("Editable media runtime animations must be an object")
            animations: dict[str, dict[WebEditableField, WebAnimationTrack]] = {}
            for layer_id, tracks_value in animations_value.items():
                layer_definition = manifest_layers.get(str(layer_id))
                if layer_definition is None or not isinstance(tracks_value, Mapping):
                    raise ValueError(f"Editable media runtime animation layer is invalid: {layer_id}")
                tracks: dict[WebEditableField, WebAnimationTrack] = {}
                for field, track_value in tracks_value.items():
                    if field not in layer_definition.editable:
                        raise ValueError(f"Layer {layer_id} does not allow animation field: {field}")
                    track = WebAnimationTrack.model_validate(track_value)
                    if track.field != field:
                        raise ValueError(
                            f"Editable media animation track key does not match: {layer_id}/{field}"
                        )
                    if track.keyframes[-1].time_ms >= definition.duration_ms:
                        raise ValueError(f"Editable media animation exceeds scene {current_scene_id}")
                    tracks[cast(WebEditableField, field)] = track
                if tracks:
                    animations[str(layer_id)] = tracks

            scene_parameter_definitions = {
                item.descriptor.id: item
                for item in spec.manifest.parameters
                if item.binding.scope == "scene"
            }
            scene_parameters_value = scene_value["parameters"]
            if not isinstance(scene_parameters_value, Mapping) or set(scene_parameters_value) != set(
                scene_parameter_definitions
            ):
                raise ValueError(
                    f"Editable media runtime scene {current_scene_id} must contain every scene parameter"
                )
            scene_parameters: dict[str, str | float | int | bool] = {}
            for parameter_id, value in scene_parameters_value.items():
                parameter = scene_parameter_definitions[str(parameter_id)]
                parameter.descriptor.validate_value(cast(JsonValue, value))
                base = definition.parameters.get(
                    parameter.descriptor.id,
                    parameter.descriptor.default,
                )
                if value != base:
                    scene_parameters[str(parameter_id)] = cast(
                        str | float | int | bool,
                        value,
                    )

            parameter_animations_value = scene_value["parameter_animations"]
            if not isinstance(parameter_animations_value, Mapping):
                raise ValueError("Editable media runtime parameter animations must be an object")
            parameter_animations: dict[str, WebParameterAnimationTrack] = {}
            all_parameter_definitions = {
                item.descriptor.id: item for item in spec.manifest.parameters
            }
            for parameter_id, track_value in parameter_animations_value.items():
                parameter = all_parameter_definitions.get(str(parameter_id))
                if parameter is None or parameter.descriptor.timeline != "keyframe":
                    raise ValueError(f"Editable parameter does not allow animation: {parameter_id}")
                parameter_track = WebParameterAnimationTrack.model_validate(track_value)
                if parameter_track.parameter_id != parameter_id:
                    raise ValueError(f"Editable parameter animation key does not match: {parameter_id}")
                if parameter_track.keyframes[-1].time_ms >= definition.duration_ms:
                    raise ValueError(f"Editable parameter animation exceeds scene {current_scene_id}")
                for keyframe in parameter_track.keyframes:
                    parameter.descriptor.validate_value(keyframe.value)
                parameter_animations[str(parameter_id)] = parameter_track

            scene_parameter_locks_value = scene_value["parameter_locks"]
            if (
                not isinstance(scene_parameter_locks_value, list)
                or any(not isinstance(value, str) for value in scene_parameter_locks_value)
                or not set(scene_parameter_locks_value).issubset(scene_parameter_definitions)
            ):
                raise ValueError(
                    f"Editable media runtime scene {current_scene_id} parameter locks are invalid"
                )
            scene_parameter_locks = tuple(
                parameter_id
                for parameter_id in scene_parameter_definitions
                if parameter_id in set(scene_parameter_locks_value)
            )

            data_value = scene_value["data"]
            if not isinstance(data_value, Mapping) or set(data_value) != set(data_fields):
                raise ValueError(
                    f"Editable media runtime scene {current_scene_id} must contain every declared data field"
                )
            scene_defaults = dict(data_defaults)
            scene_defaults.update(definition.data)
            data_overrides: dict[str, JsonValue] = {}
            for field_id, value in data_value.items():
                field = data_fields[str(field_id)]
                self.validate_data_value(field, value)
                if field.kind == "media-source" and value not in media_source_ids:
                    raise ValueError(
                        f"Data field {field_id} media source is not declared in the v4 media-sources manifest"
                    )
                if value != scene_defaults[field_id]:
                    data_overrides[str(field_id)] = cast(JsonValue, value)
            previous_scene = current.scenes.get(current_scene_id, WebSceneState())
            snapshot = WebDataSnapshot(
                source_kind=previous_scene.data_snapshot.source_kind,
                source_label=previous_scene.data_snapshot.source_label,
                values=data_overrides,
            )

            locks_value = scene_value["locks"]
            if not isinstance(locks_value, Mapping):
                raise ValueError("Editable media runtime locks must be an object")
            locks: dict[str, tuple[WebEditableField, ...]] = {}
            for layer_id, fields_value in locks_value.items():
                layer_definition = manifest_layers.get(str(layer_id))
                if layer_definition is None or not isinstance(fields_value, list):
                    raise ValueError(f"Editable media runtime lock is invalid: {layer_id}")
                if any(not isinstance(field, str) for field in fields_value):
                    raise ValueError(f"Editable media runtime lock is invalid: {layer_id}")
                unknown_fields = set(fields_value) - set(layer_definition.editable)
                if unknown_fields:
                    raise ValueError(
                        f"Editable media runtime lock contains unknown fields: {sorted(unknown_fields)}"
                    )
                locks[str(layer_id)] = tuple(
                    field for field in layer_definition.editable if field in set(fields_value)
                )
            scenes[current_scene_id] = WebSceneState(
                layers=layers,
                animations=animations,
                parameters=scene_parameters,
                parameter_animations=parameter_animations,
                parameter_locks=scene_parameter_locks,
                data_snapshot=snapshot,
                locks=locks,
            )

        return self._save_state(
            editor,
            current,
            current.model_copy(
                update={
                    "scenes": scenes,
                    "theme": theme,
                    "parameters": parameters,
                    "parameter_locks": parameter_locks,
                    "variant": WebRuntimeVariant(
                        id=variant.id,
                        width=variant.canvas.width,
                        height=variant.canvas.height,
                    ),
                    "scene_id": scene_id,
                    "playback": playback,
                }
            ),
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
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        layer = next((item for item in spec.manifest.layers if item.id == layer_id), None)
        if layer is None:
            raise ValueError(f"Editable media layer is not declared: {layer_id}")
        if field not in layer.editable:
            raise ValueError(f"Layer {layer_id} does not allow field: {field}")
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        scene_definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
        if time_ms >= scene_definition.duration_ms:
            raise ValueError("Editable media keyframe must stay inside its scene")
        if actor == "automation" and field in current_scene.locks.get(layer_id, ()):
            raise PermissionError(f"Editable media field is locked: {layer_id}/{field}")
        validated_value = self.validated_field_value(layer_id, field, value, layer.constraints.get(field))
        animations = {key: dict(tracks) for key, tracks in current_scene.animations.items()}
        layer_tracks = animations.setdefault(layer_id, {})
        existing = layer_tracks.get(field)
        keyframes = [item for item in (existing.keyframes if existing else []) if item.time_ms != time_ms]
        keyframes.append(
            WebKeyframe(
                time_ms=time_ms,
                value=validated_value,
                easing=WebEasing.model_validate(easing or {}),
            )
        )
        keyframes.sort(key=lambda item: item.time_ms)
        interpolation = "continuous" if field in CONTINUOUS_ANIMATION_FIELDS else "discrete"
        layer_tracks[field] = WebAnimationTrack(
            field=cast(WebEditableField, field),
            interpolation=cast(WebInterpolation, interpolation),
            keyframes=keyframes,
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"animations": animations})
        return self._save_state(
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
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        scene_definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
        if time_ms >= scene_definition.duration_ms:
            raise ValueError("Editable media keyframe must stay inside its scene")
        animations = {key: dict(tracks) for key, tracks in current_scene.animations.items()}
        tracks = animations.get(layer_id)
        if not tracks or field not in tracks:
            raise KeyError(f"{layer_id}/{field}/{time_ms}")
        remaining = [item for item in tracks[field].keyframes if item.time_ms != time_ms]
        if len(remaining) == len(tracks[field].keyframes):
            raise KeyError(f"{layer_id}/{field}/{time_ms}")
        if remaining:
            tracks[field] = tracks[field].model_copy(update={"keyframes": remaining})
        else:
            tracks.pop(field)
        if not tracks:
            animations.pop(layer_id, None)
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"animations": animations})
        return self._save_state(
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
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        definition = next(item for item in spec.manifest.scenes if item.id == resolved_scene_id)
        if new_time_ms < 0 or new_time_ms >= definition.duration_ms:
            raise ValueError("Editable media keyframe must stay inside its scene")
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        animations = {key: dict(tracks) for key, tracks in current_scene.animations.items()}
        track = animations.get(layer_id, {}).get(cast(WebEditableField, field))
        if track is None:
            raise KeyError(f"{layer_id}/{field}/{old_time_ms}")
        moving = next(
            (item for item in track.keyframes if item.time_ms == old_time_ms),
            None,
        )
        if moving is None:
            raise KeyError(f"{layer_id}/{field}/{old_time_ms}")
        if any(item.time_ms == new_time_ms and item.time_ms != old_time_ms for item in track.keyframes):
            raise ValueError("Editable media keyframe destination is occupied")
        keyframes = [item for item in track.keyframes if item.time_ms != old_time_ms]
        keyframes.append(moving.model_copy(update={"time_ms": new_time_ms}))
        keyframes.sort(key=lambda item: item.time_ms)
        animations[layer_id][cast(WebEditableField, field)] = track.model_copy(
            update={"keyframes": keyframes}
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"animations": animations})
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

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
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        definition = spec.manifest.parameter_for(parameter_id)
        if definition.descriptor.timeline != "keyframe":
            raise ValueError(f"Editable parameter is not animatable: {parameter_id}")
        definition.descriptor.validate_value(value)
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
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
            "continuous"
            if definition.descriptor.kind in {"number", "integer"}
            else "discrete"
        )
        animations[parameter_id] = WebParameterAnimationTrack(
            parameter_id=parameter_id,
            interpolation=interpolation,
            keyframes=keyframes,
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"parameter_animations": animations})
        return self._save_state(
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
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        spec.manifest.parameter_for(parameter_id)
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
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
        return self._save_state(
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
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        spec.manifest.parameter_for(parameter_id)
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
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
        return self._save_state(
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
        editor, _asset, spec, current = self._clip_context(
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
            resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
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
        return self._save_state(editor, current, candidate)

    def update_theme(
        self,
        sequence_id: str,
        clip_id: str,
        changes: Mapping[str, str | float],
        *,
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        variables = {item.id: item for item in spec.manifest.theme_variables}
        theme = dict(current.theme)
        for variable_id, value in changes.items():
            variable = variables.get(variable_id)
            if variable is None:
                raise ValueError(f"Editable media theme variable is not declared: {variable_id}")
            if variable.kind == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"Theme variable {variable_id} must be numeric")
            elif not isinstance(value, str):
                raise ValueError(f"Theme variable {variable_id} must be text")
            self.validate_constraint(variable_id, "theme", value, variable.constraints)
            theme[variable_id] = value
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"theme": theme}),
        )

    def update_data(
        self,
        sequence_id: str,
        clip_id: str,
        values: Mapping[str, object],
        *,
        scene_id: str | None = None,
        source_kind: Literal["inline", "file", "api"] = "inline",
        source_label: str = "",
        expected_revision: int | None = None,
    ) -> WebClipState:
        editor, _asset, spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        fields = {item.id: item for item in spec.manifest.data_fields}
        merged = dict(current_scene.data_snapshot.values)
        media_source_ids = web_contract.media_source_ids(
            web_files.web_package_root(
                self.repository.catalog.resolve_asset_path(_asset),
                spec.manifest,
            ),
            spec.manifest,
        )
        for field_id, value in values.items():
            field = fields.get(field_id)
            if field is None:
                raise ValueError(f"Editable media data field is not declared: {field_id}")
            self.validate_data_value(field, value)
            if field.kind == "media-source":
                if value not in media_source_ids:
                    raise ValueError(
                        f"Data field {field_id} media source is not declared in the v4 media-sources manifest"
                    )
            merged[field_id] = value
        snapshot = WebDataSnapshot(
            source_kind=source_kind,
            source_label=source_label,
            values=merged,
        )
        scenes = dict(current.scenes)
        scenes[resolved_scene_id] = current_scene.model_copy(update={"data_snapshot": snapshot})
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def update_data_from_file(
        self,
        sequence_id: str,
        clip_id: str,
        source: str | Path,
        *,
        scene_id: str | None = None,
        field_id: str | None = None,
        expected_revision: int | None = None,
    ) -> WebClipState:
        path = Path(source).expanduser().resolve(strict=True)
        suffix = path.suffix.lower()
        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        elif suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                payload = list(csv.DictReader(stream))
        else:
            raise ValueError("Editable media data snapshots accept .json or .csv files")
        _editor, _asset, spec, _current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        declared = {item.id for item in spec.manifest.data_fields}
        if field_id is not None:
            values = {field_id: payload}
        elif isinstance(payload, dict) and set(payload).issubset(declared):
            values = payload
        elif len(declared) == 1:
            values = {next(iter(declared)): payload}
        else:
            raise ValueError(
                "Data file must contain declared field IDs or specify field_id when multiple fields exist"
            )
        return self.update_data(
            sequence_id,
            clip_id,
            values,
            scene_id=scene_id,
            source_kind="file",
            source_label=str(path),
            expected_revision=expected_revision,
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
        editor, _asset, spec, current = self._clip_context(
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
        resolved_scene_id = self._scene_id(current, spec.manifest, scene_id)
        current_scene = current.scenes.get(resolved_scene_id, WebSceneState())
        locks = {key: set(value) for key, value in current_scene.locks.items()}
        target = locks.setdefault(layer_id, set())
        if locked:
            target.update(fields)
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
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"scenes": scenes, "scene_id": resolved_scene_id}),
        )

    def runtime_state(
        self,
        sequence_id: str,
        clip_id: str,
    ) -> dict:
        _editor, _asset, spec, current = self._clip_context(sequence_id, clip_id, None)
        return web_runtime_state(current, spec.manifest)

    def _clip_context(
        self,
        sequence_id: str,
        clip_id: str,
        expected_revision: int | None,
    ):
        editor = self._timeline(sequence_id)
        state = editor.state
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self.repository.catalog.get_asset(clip.asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Clip is not editable web media")
        spec = self._packages.inspect_asset(asset.id)
        current = state.web_states[clip_id]
        if expected_revision is not None and current.revision != expected_revision:
            raise RuntimeError(
                f"Editable media revision conflict: expected {expected_revision}, current {current.revision}"
            )
        return editor, asset, spec, current

    @staticmethod
    def _scene_id(
        state: WebClipState,
        manifest: EditableMediaManifest,
        scene_id: str | None,
    ) -> str:
        resolved = scene_id or state.scene_id or manifest.scenes[0].id
        if resolved not in {item.id for item in manifest.scenes}:
            raise ValueError(f"Editable media scene does not exist: {resolved}")
        return resolved

    def _save_state(
        self,
        editor: TimelineEditor,
        current: WebClipState,
        candidate: WebClipState,
    ) -> WebClipState:
        try:
            clip = next(item for item in editor.state.clips if item.id == current.clip_id)
        except StopIteration as error:
            raise KeyError(current.clip_id) from error
        asset = self.repository.catalog.get_asset(clip.asset_id)
        spec = self.repository.web.get_web_asset_spec(asset.id)
        package_root = web_files.web_package_root(
            self.repository.catalog.resolve_asset_path(asset),
            spec.manifest,
        )
        web_contract.validate_media_bindings(
            spec.manifest,
            web_contract.read_media_sources(package_root, spec.manifest),
            candidate,
        )
        web_contract.validate_clip_state_contract(spec.manifest, candidate)
        updated = candidate.model_copy(update={"revision": current.revision + 1})
        editor.set_web_clip_state(updated, expected_revision=current.revision)
        return editor.state.web_states[current.clip_id]

    @staticmethod
    def validated_field_value(
        layer_id: str,
        field: str,
        value: object,
        constraint: WebFieldConstraint | None,
    ) -> JsonValue:
        candidate = WebLayerOverride.model_validate({field: value})
        validated = getattr(candidate, field)
        WebClipEditingService.validate_constraint(layer_id, field, validated, constraint)
        return cast(JsonValue, validated)

    @staticmethod
    def validate_data_value(field: WebDataField, value: object) -> None:
        field_id = field.id
        kind = field.kind
        valid = {
            "string": isinstance(value, str),
            "date": isinstance(value, str),
            "media-source": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "list": isinstance(value, list),
            "table": isinstance(value, list) and all(isinstance(row, dict) for row in value),
            "json": isinstance(value, (dict, list, str, int, float, bool)) or value is None,
        }.get(kind, False)
        if not valid:
            raise ValueError(f"Data field {field_id} does not match kind {kind}")
        if kind == "table" and field.columns:
            if not isinstance(value, list):
                raise ValueError(f"Data field {field_id} table value must be a list")
            columns = {item.id: item.kind for item in field.columns}
            for index, row in enumerate(value):
                if not isinstance(row, dict):
                    raise ValueError(f"Data field {field_id} row {index} must be an object")
                missing = set(columns) - set(row)
                unknown = set(row) - set(columns)
                if missing or unknown:
                    raise ValueError(
                        f"Data field {field_id} row {index} columns do not match; "
                        f"missing={sorted(missing)}, unknown={sorted(unknown)}"
                    )
                for column_id, column_kind in columns.items():
                    cell = row[column_id]
                    cell_valid = {
                        "string": isinstance(cell, str),
                        "date": isinstance(cell, str),
                        "media-source": isinstance(cell, str),
                        "number": isinstance(cell, (int, float)) and not isinstance(cell, bool),
                        "boolean": isinstance(cell, bool),
                    }[column_kind]
                    if not cell_valid:
                        raise ValueError(
                            f"Data field {field_id} row {index} column {column_id} "
                            f"does not match kind {column_kind}"
                        )

    @staticmethod
    def validate_constraint(
        layer_id: str,
        field: str,
        value: object,
        constraint: WebFieldConstraint | None,
    ) -> None:
        if value is None or constraint is None:
            return
        if constraint.choices and str(value) not in constraint.choices:
            raise ValueError(f"Layer {layer_id} field {field} is outside its choices")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if constraint.minimum is not None and value < constraint.minimum:
                raise ValueError(f"Layer {layer_id} field {field} is below its minimum")
            if constraint.maximum is not None and value > constraint.maximum:
                raise ValueError(f"Layer {layer_id} field {field} exceeds its maximum")
            if constraint.step is not None:
                origin = constraint.minimum or 0.0
                steps = (float(value) - origin) / constraint.step
                if abs(steps - round(steps)) > 1e-7:
                    raise ValueError(f"Layer {layer_id} field {field} does not match its step")

    def set_batch_name(
        self,
        sequence_id: str,
        clip_id: str,
        name: str,
        *,
        expected_revision: int,
    ) -> WebClipState:
        editor, _asset, _spec, current = self._clip_context(
            sequence_id,
            clip_id,
            expected_revision,
        )
        return self._save_state(
            editor,
            current,
            current.model_copy(update={"batch_name": name}),
        )
