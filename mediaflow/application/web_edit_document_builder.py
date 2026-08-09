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
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebClipState,
    WebEditDocument,
    WebFieldConstraint,
    WebSceneState,
    web_runtime_state,
)


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
    runtime_layers = cast(dict[str, dict[str, JsonValue]], runtime_scene["layers"])
    current_scene = state.scenes.get(scene_id, WebSceneState())
    fields: list[EditorFieldValue] = []

    numeric_fields = {
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
    integer_fields = {
        "z_index",
        "enter_ms",
        "exit_ms",
        "delay_ms",
        "duration_ms",
    }
    interval_fields = {"enter_ms", "exit_ms", "delay_ms", "duration_ms"}
    fallback_defaults: dict[str, EditorFieldScalar] = {
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
        "exit_ms": scene_definition.duration_ms,
        "delay_ms": 0,
        "duration_ms": 0,
    }
    for layer in manifest.layers:
        defaults = manifest.layer_values_for(variant.id, layer.id)
        values = runtime_layers[layer.id]
        locked_fields = set(current_scene.locks.get(layer.id, ()))
        for field in layer.editable:
            constraint = layer.constraints.get(field, WebFieldConstraint())
            kind: EditorFieldKind = (
                "boolean"
                if field == "visible"
                else "integer"
                if field in integer_fields
                else "number"
                if field in numeric_fields
                else "color"
                if field == "color"
                else "string"
            )
            control: EditorFieldControl = (
                "toggle"
                if kind == "boolean"
                else "color"
                if kind == "color"
                else "slider"
                if kind in {"number", "integer"}
                and constraint.minimum is not None
                and constraint.maximum is not None
                else "number"
                if kind in {"number", "integer"}
                else "text"
            )
            default_value = cast(EditorFieldScalar | None, defaults.get(field))
            if default_value is None:
                default_value = fallback_defaults.get(field)
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
                        control=control,
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
                        timeline=("interval" if field in interval_fields else "keyframe"),
                    ),
                    value=values.get(field, cast(JsonValue, default_value)),
                    locked=field in locked_fields,
                )
            )

    runtime_parameters = cast(dict[str, JsonValue], runtime["parameters"])
    scene_parameters = cast(dict[str, JsonValue], runtime_scene["parameters"])
    for parameter_definition in manifest.parameters:
        descriptor = parameter_definition.descriptor
        scope = parameter_definition.binding.scope
        values = runtime_parameters if scope == "global" else scene_parameters
        locked = (
            descriptor.id in state.parameter_locks
            if scope == "global"
            else descriptor.id in current_scene.parameter_locks
        )
        path = (
            f"parameters.{descriptor.id}"
            if scope == "global"
            else f"scenes.{scene_id}.parameters.{descriptor.id}"
        )
        fields.append(
            EditorFieldValue(
                path=path,
                target="parameter",
                source_id=descriptor.id,
                descriptor=descriptor,
                value=values[descriptor.id],
                locked=locked,
            )
        )

    theme_values = cast(dict[str, JsonValue], runtime["theme"])
    for theme_definition in manifest.theme_variables:
        theme_kind: EditorFieldKind = (
            "color"
            if theme_definition.kind == "color"
            else "number"
            if theme_definition.kind == "number"
            else "string"
        )
        theme_control: EditorFieldControl = (
            "color"
            if theme_kind == "color"
            else "number"
            if theme_kind == "number"
            else "text"
        )
        fields.append(
            EditorFieldValue(
                path=f"theme.{theme_definition.id}",
                target="theme",
                source_id=theme_definition.id,
                descriptor=EditorFieldDescriptor(
                    id=theme_definition.id,
                    label=theme_definition.name,
                    description="",
                    group="主题",
                    kind=theme_kind,
                    control=theme_control,
                    default=cast(EditorFieldScalar, theme_definition.default),
                    unit=None,
                    constraints=EditorFieldConstraints(
                        minimum=(
                            theme_definition.constraints.minimum
                            if theme_definition.constraints is not None
                            else None
                        ),
                        maximum=(
                            theme_definition.constraints.maximum
                            if theme_definition.constraints is not None
                            else None
                        ),
                        step=(
                            theme_definition.constraints.step
                            if theme_definition.constraints is not None
                            else None
                        ),
                        choices=(
                            [
                                EditorFieldChoice(value=value, label=str(value))
                                for value in theme_definition.constraints.choices
                            ]
                            if theme_definition.constraints is not None
                            else []
                        ),
                    ),
                    options_source=None,
                    timeline="none",
                ),
                value=theme_values[theme_definition.id],
            )
        )

    data_values = cast(dict[str, JsonValue], runtime_scene["data"])
    for data_definition in manifest.data_fields:
        default_text = json.dumps(
            data_definition.default,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        value_text = json.dumps(
            data_values[data_definition.id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        fields.append(
            EditorFieldValue(
                path=f"scenes.{scene_id}.data.{data_definition.id}",
                target="data",
                source_id=data_definition.id,
                descriptor=EditorFieldDescriptor(
                    id=data_definition.id,
                    label=data_definition.name,
                    description=f"editable-media data kind: {data_definition.kind}",
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
    return WebEditDocument(
        clip_id=clip_id,
        scene_id=scene_id,
        variant_id=variant.id,
        revision=state.revision,
        scene_duration_ms=scene_definition.duration_ms,
        fields=fields,
    )
