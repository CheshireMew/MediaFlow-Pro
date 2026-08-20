from __future__ import annotations

from collections.abc import Mapping, Set
from typing import cast

from pydantic import JsonValue

from mediaflow.application.web_field_validation import WebFieldValidator
from mediaflow.domain.web_manifest import EditableMediaManifest
from mediaflow.domain.web_manifest_primitives import WebEditableField
from mediaflow.domain.web_state import (
    WebAnimationTrack,
    WebClipState,
    WebDataSnapshot,
    WebLayerOverride,
    WebParameterAnimationTrack,
    WebRuntimePlayback,
    WebRuntimeVariant,
    WebSceneState,
)


class WebRuntimeStateCommit:
    """Parse a complete browser runtime snapshot into canonical clip state."""

    @classmethod
    def candidate(
        cls,
        current: WebClipState,
        manifest: EditableMediaManifest,
        runtime_state: Mapping[str, object],
        media_source_ids: Set[str],
    ) -> WebClipState:
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
        variant = manifest.variant_for(str(variant_value.get("id") or ""))
        if (
            int(variant_value.get("width") or 0) != variant.canvas.width
            or int(variant_value.get("height") or 0) != variant.canvas.height
        ):
            raise ValueError("Editable media runtime variant dimensions do not match the manifest")

        scene_id = str(runtime_state["scene_id"])
        scene_definitions = {item.id: item for item in manifest.scenes}
        if scene_id not in scene_definitions:
            raise ValueError(f"Editable media runtime scene is not declared: {scene_id}")
        scenes_value = runtime_state["scenes"]
        if not isinstance(scenes_value, Mapping) or set(scenes_value) != set(scene_definitions):
            raise ValueError("Editable media runtime must return every declared scene")

        theme = cls._theme(runtime_state, manifest)
        parameters, parameter_locks = cls._global_parameters(runtime_state, manifest)
        playback_value = runtime_state["playback"]
        if not isinstance(playback_value, Mapping):
            raise ValueError("Editable media runtime playback must be an object")
        playback = WebRuntimePlayback.model_validate(dict(playback_value))
        scenes = cls._scenes(
            current,
            manifest,
            scenes_value,
            media_source_ids,
            variant.id,
        )
        return current.model_copy(
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
        )

    @staticmethod
    def _theme(
        runtime_state: Mapping[str, object],
        manifest: EditableMediaManifest,
    ) -> dict[str, str | float]:
        theme_bindings = {item.id: item.css_variable for item in manifest.theme_variables}
        if runtime_state["theme_bindings"] != theme_bindings:
            raise ValueError("Editable media runtime theme bindings do not match the manifest")
        theme_value = runtime_state["theme"]
        if not isinstance(theme_value, Mapping):
            raise ValueError("Editable media runtime theme must be an object")
        defaults = {item.id: item.default for item in manifest.theme_variables}
        if set(theme_value) != set(defaults):
            raise ValueError("Editable media runtime theme must contain every declared variable")
        variables = {item.id: item for item in manifest.theme_variables}
        theme: dict[str, str | float] = {}
        for variable_id, value in theme_value.items():
            variable = variables[str(variable_id)]
            if variable.kind == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"Theme variable {variable_id} must be numeric")
            elif not isinstance(value, str):
                raise ValueError(f"Theme variable {variable_id} must be text")
            WebFieldValidator.constraint(
                str(variable_id),
                "theme",
                value,
                variable.constraints,
            )
            if value != defaults[variable_id]:
                theme[str(variable_id)] = cast(str | float, value)
        return theme

    @staticmethod
    def _global_parameters(
        runtime_state: Mapping[str, object],
        manifest: EditableMediaManifest,
    ) -> tuple[dict[str, str | float | int | bool], tuple[str, ...]]:
        bindings = {
            item.descriptor.id: item.binding.css_variable
            for item in manifest.parameters
            if item.binding.css_variable is not None
        }
        if runtime_state["parameter_bindings"] != bindings:
            raise ValueError("Editable media runtime parameter bindings do not match the manifest")
        definitions = {
            item.descriptor.id: item for item in manifest.parameters if item.binding.scope == "global"
        }
        values = runtime_state["parameters"]
        if not isinstance(values, Mapping) or set(values) != set(definitions):
            raise ValueError("Editable media runtime parameters must contain every global parameter")
        parameters: dict[str, str | float | int | bool] = {}
        for parameter_id, value in values.items():
            definition = definitions[str(parameter_id)]
            definition.descriptor.validate_value(cast(JsonValue, value))
            if value != definition.descriptor.default:
                parameters[str(parameter_id)] = cast(str | float | int | bool, value)
        locks_value = runtime_state["parameter_locks"]
        if (
            not isinstance(locks_value, list)
            or any(not isinstance(value, str) for value in locks_value)
            or not set(locks_value).issubset(definitions)
        ):
            raise ValueError("Editable media runtime global parameter locks are invalid")
        locked = set(locks_value)
        return parameters, tuple(parameter_id for parameter_id in definitions if parameter_id in locked)

    @classmethod
    def _scenes(
        cls,
        current: WebClipState,
        manifest: EditableMediaManifest,
        scenes_value: Mapping[object, object],
        media_source_ids: Set[str],
        variant_id: str,
    ) -> dict[str, WebSceneState]:
        definitions = {item.id: item for item in manifest.scenes}
        manifest_layers = {item.id: item for item in manifest.layers}
        data_fields = {item.id: item for item in manifest.data_fields}
        data_defaults = {item.id: item.default for item in manifest.data_fields}
        scene_parameter_definitions = {
            item.descriptor.id: item for item in manifest.parameters if item.binding.scope == "scene"
        }
        all_parameter_definitions = {item.descriptor.id: item for item in manifest.parameters}
        scenes: dict[str, WebSceneState] = {}
        for scene_id, definition in definitions.items():
            scene_value = scenes_value[scene_id]
            if not isinstance(scene_value, Mapping) or set(scene_value) != {
                "layers",
                "animations",
                "parameters",
                "parameter_animations",
                "parameter_locks",
                "data",
                "locks",
            }:
                raise ValueError(f"Editable media runtime scene {scene_id} is incomplete")
            scenes[scene_id] = WebSceneState(
                layers=cls._layers(
                    scene_id,
                    scene_value["layers"],
                    manifest,
                    manifest_layers,
                    media_source_ids,
                    variant_id,
                ),
                animations=cls._animations(
                    scene_id,
                    definition.duration_ms,
                    scene_value["animations"],
                    manifest_layers,
                ),
                parameters=cls._scene_parameters(
                    scene_id,
                    definition.parameters,
                    scene_value["parameters"],
                    scene_parameter_definitions,
                ),
                parameter_animations=cls._parameter_animations(
                    scene_id,
                    definition.duration_ms,
                    scene_value["parameter_animations"],
                    all_parameter_definitions,
                ),
                parameter_locks=cls._scene_parameter_locks(
                    scene_id,
                    scene_value["parameter_locks"],
                    scene_parameter_definitions,
                ),
                data_snapshot=cls._data_snapshot(
                    scene_id,
                    scene_value["data"],
                    data_fields,
                    data_defaults | dict(definition.data),
                    media_source_ids,
                    current.scenes.get(scene_id, WebSceneState()),
                ),
                locks=cls._layer_locks(scene_value["locks"], manifest_layers),
            )
        return scenes

    @staticmethod
    def _layers(scene_id, value, manifest, definitions, media_source_ids, variant_id):
        if not isinstance(value, Mapping) or set(value) != set(definitions):
            raise ValueError(f"Editable media runtime scene {scene_id} must contain every layer")
        layers: dict[str, WebLayerOverride] = {}
        for layer_id, definition in definitions.items():
            layer_value = value[layer_id]
            if not isinstance(layer_value, Mapping):
                raise ValueError(f"Editable media runtime layer {scene_id}/{layer_id} must be an object")
            unknown = set(layer_value) - set(WebLayerOverride.model_fields)
            if unknown:
                raise ValueError(
                    f"Editable media runtime layer {scene_id}/{layer_id} contains unknown fields: "
                    f"{sorted(unknown)}"
                )
            base = manifest.layer_values_for(variant_id, layer_id)
            overrides = {
                str(field): field_value
                for field, field_value in layer_value.items()
                if field not in base or field_value != base[field]
            }
            disallowed = set(overrides) - set(definition.editable)
            if disallowed:
                raise ValueError(
                    f"Editable media runtime layer {scene_id}/{layer_id} changes non-editable fields: "
                    f"{sorted(disallowed)}"
                )
            candidate = WebLayerOverride.model_validate(overrides)
            if candidate.image is not None and candidate.image not in media_source_ids:
                raise ValueError(f"Layer {layer_id} image is not declared in the v4 media-sources manifest")
            for field in overrides:
                WebFieldValidator.constraint(
                    layer_id,
                    field,
                    getattr(candidate, field),
                    definition.constraints.get(cast(WebEditableField, field)),
                )
            if candidate.changed_fields():
                layers[layer_id] = candidate
        return layers

    @staticmethod
    def _animations(scene_id, duration_ms, value, definitions):
        if not isinstance(value, Mapping):
            raise ValueError("Editable media runtime animations must be an object")
        animations: dict[str, dict[WebEditableField, WebAnimationTrack]] = {}
        for layer_id, tracks_value in value.items():
            definition = definitions.get(str(layer_id))
            if definition is None or not isinstance(tracks_value, Mapping):
                raise ValueError(f"Editable media runtime animation layer is invalid: {layer_id}")
            tracks: dict[WebEditableField, WebAnimationTrack] = {}
            for field, track_value in tracks_value.items():
                if field not in definition.editable:
                    raise ValueError(f"Layer {layer_id} does not allow animation field: {field}")
                track = WebAnimationTrack.model_validate(track_value)
                if track.field != field:
                    raise ValueError(f"Editable media animation track key does not match: {layer_id}/{field}")
                if track.keyframes[-1].time_ms >= duration_ms:
                    raise ValueError(f"Editable media animation exceeds scene {scene_id}")
                tracks[cast(WebEditableField, field)] = track
            if tracks:
                animations[str(layer_id)] = tracks
        return animations

    @staticmethod
    def _scene_parameters(scene_id, defaults, value, definitions):
        if not isinstance(value, Mapping) or set(value) != set(definitions):
            raise ValueError(f"Editable media runtime scene {scene_id} must contain every scene parameter")
        parameters: dict[str, str | float | int | bool] = {}
        for parameter_id, parameter_value in value.items():
            parameter = definitions[str(parameter_id)]
            parameter.descriptor.validate_value(cast(JsonValue, parameter_value))
            base = defaults.get(parameter.descriptor.id, parameter.descriptor.default)
            if parameter_value != base:
                parameters[str(parameter_id)] = cast(str | float | int | bool, parameter_value)
        return parameters

    @staticmethod
    def _parameter_animations(scene_id, duration_ms, value, definitions):
        if not isinstance(value, Mapping):
            raise ValueError("Editable media runtime parameter animations must be an object")
        animations: dict[str, WebParameterAnimationTrack] = {}
        for parameter_id, track_value in value.items():
            parameter = definitions.get(str(parameter_id))
            if parameter is None or parameter.descriptor.timeline != "keyframe":
                raise ValueError(f"Editable parameter does not allow animation: {parameter_id}")
            track = WebParameterAnimationTrack.model_validate(track_value)
            if track.parameter_id != parameter_id:
                raise ValueError(f"Editable parameter animation key does not match: {parameter_id}")
            if track.keyframes[-1].time_ms >= duration_ms:
                raise ValueError(f"Editable parameter animation exceeds scene {scene_id}")
            for keyframe in track.keyframes:
                parameter.descriptor.validate_value(keyframe.value)
            animations[str(parameter_id)] = track
        return animations

    @staticmethod
    def _scene_parameter_locks(scene_id, value, definitions):
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) for item in value)
            or not set(value).issubset(definitions)
        ):
            raise ValueError(f"Editable media runtime scene {scene_id} parameter locks are invalid")
        locked = set(value)
        return tuple(parameter_id for parameter_id in definitions if parameter_id in locked)

    @staticmethod
    def _data_snapshot(scene_id, value, fields, defaults, media_source_ids, previous):
        if not isinstance(value, Mapping) or set(value) != set(fields):
            raise ValueError(
                f"Editable media runtime scene {scene_id} must contain every declared data field"
            )
        overrides: dict[str, JsonValue] = {}
        for field_id, field_value in value.items():
            field = fields[str(field_id)]
            WebFieldValidator.data_value(field, field_value)
            if field.kind == "media-source" and field_value not in media_source_ids:
                raise ValueError(
                    f"Data field {field_id} media source is not declared in the v4 media-sources manifest"
                )
            if field_value != defaults[field_id]:
                overrides[str(field_id)] = cast(JsonValue, field_value)
        return WebDataSnapshot(
            source_kind=previous.data_snapshot.source_kind,
            source_label=previous.data_snapshot.source_label,
            values=overrides,
        )

    @staticmethod
    def _layer_locks(value, definitions):
        if not isinstance(value, Mapping):
            raise ValueError("Editable media runtime locks must be an object")
        locks: dict[str, tuple[WebEditableField, ...]] = {}
        for layer_id, fields_value in value.items():
            definition = definitions.get(str(layer_id))
            if definition is None or not isinstance(fields_value, list):
                raise ValueError(f"Editable media runtime lock is invalid: {layer_id}")
            if any(not isinstance(field, str) for field in fields_value):
                raise ValueError(f"Editable media runtime lock is invalid: {layer_id}")
            unknown_fields = set(fields_value) - set(definition.editable)
            if unknown_fields:
                raise ValueError(
                    f"Editable media runtime lock contains unknown fields: {sorted(unknown_fields)}"
                )
            selected = set(fields_value)
            locks[str(layer_id)] = tuple(field for field in definition.editable if field in selected)
        return locks
