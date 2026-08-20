from __future__ import annotations

import json
from typing import cast

from pydantic import JsonValue

from mediaflow.domain.editor_fields import (
    EditorFieldChoice,
    EditorFieldConstraints,
    EditorFieldControl,
    EditorFieldDescriptor,
    EditorFieldKind,
    EditorFieldScalar,
    EditorFieldValue,
)
from mediaflow.domain.web_manifest import EditableMediaManifest
from mediaflow.domain.web_manifest_primitives import WebFieldConstraint
from mediaflow.domain.web_state import (
    WebClipState,
    WebEditDocument,
    WebSceneState,
    web_runtime_state,
)

NUMERIC_LAYER_FIELDS = frozenset(
    {
        "font_size",
        "x",
        "y",
        "width",
        "height",
        "rotation",
        "opacity",
        "z_index",
        "enter_ms",
        "exit_ms",
        "delay_ms",
        "duration_ms",
    }
)
INTEGER_LAYER_FIELDS = frozenset(
    {"z_index", "enter_ms", "exit_ms", "delay_ms", "duration_ms"}
)
INTERVAL_LAYER_FIELDS = frozenset(
    {"enter_ms", "exit_ms", "delay_ms", "duration_ms"}
)


def _layer_field_kind(field: str) -> EditorFieldKind:
    if field == "visible":
        return "boolean"
    if field in INTEGER_LAYER_FIELDS:
        return "integer"
    if field in NUMERIC_LAYER_FIELDS:
        return "number"
    if field == "color":
        return "color"
    return "string"


def _layer_field_control(
    kind: EditorFieldKind,
    constraint: WebFieldConstraint,
) -> EditorFieldControl:
    if kind == "boolean":
        return "toggle"
    if kind == "color":
        return "color"
    if kind in {"number", "integer"}:
        if constraint.minimum is not None and constraint.maximum is not None:
            return "slider"
        return "number"
    return "text"


def _layer_fallback_defaults(scene_duration_ms: int) -> dict[str, EditorFieldScalar]:
    return {
        "content": "",
        "color": "",
        "font_family": "",
        "image": "",
        "font_size": 0.0,
        "x": 0.0,
        "y": 0.0,
        "width": 0.0,
        "height": 0.0,
        "rotation": 0.0,
        "opacity": 1.0,
        "z_index": 0,
        "visible": True,
        "enter_ms": 0,
        "exit_ms": scene_duration_ms,
        "delay_ms": 0,
        "duration_ms": 0,
    }


def _layer_fields(
    manifest: EditableMediaManifest,
    *,
    scene_id: str,
    scene_duration_ms: int,
    variant_id: str,
    runtime_layers: dict[str, dict[str, JsonValue]],
    current_scene: WebSceneState,
) -> list[EditorFieldValue]:
    fields: list[EditorFieldValue] = []
    fallbacks = _layer_fallback_defaults(scene_duration_ms)
    for layer in manifest.layers:
        defaults = manifest.layer_values_for(variant_id, layer.id)
        values = runtime_layers[layer.id]
        locked_fields = set(current_scene.locks.get(layer.id, ()))
        for field in layer.editable:
            constraint = layer.constraints.get(field, WebFieldConstraint())
            kind = _layer_field_kind(field)
            default_value = cast(EditorFieldScalar | None, defaults.get(field))
            default_value = default_value if default_value is not None else fallbacks.get(field)
            if default_value is None:
                raise RuntimeError(f"Editable layer field has no default: {field}")
            if (
                kind in {"number", "integer"}
                and constraint.minimum is not None
                and cast(float, default_value) < constraint.minimum
            ):
                default_value = (
                    int(constraint.minimum)
                    if kind == "integer"
                    else float(constraint.minimum)
                )
            fields.append(
                EditorFieldValue(
                    path=f"scenes.{scene_id}.layers.{layer.id}.{field}",
                    target="layer",
                    source_id=f"{layer.id}.{field}",
                    descriptor=EditorFieldDescriptor(
                        id=field,
                        label=f"{layer.name} · {field}",
                        description="",
                        group=layer.name,
                        kind=kind,
                        control=_layer_field_control(kind, constraint),
                        default=cast(EditorFieldScalar, default_value),
                        unit=None,
                        constraints=EditorFieldConstraints(
                            minimum=constraint.minimum,
                            maximum=constraint.maximum,
                            step=constraint.step,
                            choices=[
                                EditorFieldChoice(value=value, label=str(value))
                                for value in constraint.choices
                            ],
                        ),
                        options_source=None,
                        timeline=("interval" if field in INTERVAL_LAYER_FIELDS else "keyframe"),
                    ),
                    value=values.get(field, cast(JsonValue, default_value)),
                    locked=field in locked_fields,
                )
            )
    return fields


def _parameter_fields(
    manifest: EditableMediaManifest,
    state: WebClipState,
    current_scene: WebSceneState,
    *,
    scene_id: str,
    runtime_parameters: dict[str, JsonValue],
    scene_parameters: dict[str, JsonValue],
) -> list[EditorFieldValue]:
    fields: list[EditorFieldValue] = []
    for definition in manifest.parameters:
        descriptor = definition.descriptor
        global_scope = definition.binding.scope == "global"
        values = runtime_parameters if global_scope else scene_parameters
        locked = (
            descriptor.id in state.parameter_locks
            if global_scope
            else descriptor.id in current_scene.parameter_locks
        )
        fields.append(
            EditorFieldValue(
                path=(
                    f"parameters.{descriptor.id}"
                    if global_scope
                    else f"scenes.{scene_id}.parameters.{descriptor.id}"
                ),
                target="parameter",
                source_id=descriptor.id,
                descriptor=descriptor,
                value=values[descriptor.id],
                locked=locked,
            )
        )
    return fields


def _theme_fields(
    manifest: EditableMediaManifest,
    theme_values: dict[str, JsonValue],
) -> list[EditorFieldValue]:
    fields: list[EditorFieldValue] = []
    for definition in manifest.theme_variables:
        kind: EditorFieldKind = (
            "color"
            if definition.kind == "color"
            else "number"
            if definition.kind == "number"
            else "string"
        )
        constraint = definition.constraints
        fields.append(
            EditorFieldValue(
                path=f"theme.{definition.id}",
                target="theme",
                source_id=definition.id,
                descriptor=EditorFieldDescriptor(
                    id=definition.id,
                    label=definition.name,
                    description="",
                    group="主题",
                    kind=kind,
                    control=(
                        "color" if kind == "color" else "number" if kind == "number" else "text"
                    ),
                    default=cast(EditorFieldScalar, definition.default),
                    unit=None,
                    constraints=EditorFieldConstraints(
                        minimum=constraint.minimum if constraint is not None else None,
                        maximum=constraint.maximum if constraint is not None else None,
                        step=constraint.step if constraint is not None else None,
                        choices=(
                            [
                                EditorFieldChoice(value=value, label=str(value))
                                for value in constraint.choices
                            ]
                            if constraint is not None
                            else []
                        ),
                    ),
                    options_source=None,
                    timeline="none",
                ),
                value=theme_values[definition.id],
            )
        )
    return fields


def _data_fields(
    manifest: EditableMediaManifest,
    *,
    scene_id: str,
    data_values: dict[str, JsonValue],
) -> list[EditorFieldValue]:
    fields: list[EditorFieldValue] = []
    for definition in manifest.data_fields:
        default_text = json.dumps(
            definition.default,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        value_text = json.dumps(
            data_values[definition.id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        fields.append(
            EditorFieldValue(
                path=f"scenes.{scene_id}.data.{definition.id}",
                target="data",
                source_id=definition.id,
                descriptor=EditorFieldDescriptor(
                    id=definition.id,
                    label=definition.name,
                    description=f"editable-media data kind: {definition.kind}",
                    group="数据",
                    kind="string",
                    control="text",
                    default=default_text,
                    unit=None,
                    constraints=EditorFieldConstraints(),
                    options_source=None,
                    timeline="none",
                ),
                value=value_text,
            )
        )
    return fields


def build_web_edit_document(
    *,
    clip_id: str,
    manifest: EditableMediaManifest,
    state: WebClipState,
    scene_id: str,
) -> WebEditDocument:
    """Build the generic editor-field document for one resolved web scene."""

    scene_definition = next(item for item in manifest.scenes if item.id == scene_id)
    variant = manifest.variant_for(state.variant.id if state.variant is not None else None)
    runtime = web_runtime_state(state, manifest)
    runtime_scene = cast(
        dict[str, object],
        cast(dict[str, object], runtime["scenes"])[scene_id],
    )
    current_scene = state.scenes.get(scene_id, WebSceneState())
    fields = _layer_fields(
        manifest,
        scene_id=scene_id,
        scene_duration_ms=scene_definition.duration_ms,
        variant_id=variant.id,
        runtime_layers=cast(dict[str, dict[str, JsonValue]], runtime_scene["layers"]),
        current_scene=current_scene,
    )
    fields.extend(
        _parameter_fields(
            manifest,
            state,
            current_scene,
            scene_id=scene_id,
            runtime_parameters=cast(dict[str, JsonValue], runtime["parameters"]),
            scene_parameters=cast(dict[str, JsonValue], runtime_scene["parameters"]),
        )
    )
    fields.extend(_theme_fields(manifest, cast(dict[str, JsonValue], runtime["theme"])))
    fields.extend(
        _data_fields(
            manifest,
            scene_id=scene_id,
            data_values=cast(dict[str, JsonValue], runtime_scene["data"]),
        )
    )
    return WebEditDocument(
        clip_id=clip_id,
        scene_id=scene_id,
        variant_id=variant.id,
        revision=state.revision,
        scene_duration_ms=scene_definition.duration_ms,
        fields=fields,
    )