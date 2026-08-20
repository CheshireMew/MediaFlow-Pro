from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal, cast

from pydantic import Field, JsonValue, field_validator, model_validator

from .editor_fields import EditorFieldValue
from .model_base import DomainModel, now_ms
from .web_manifest import EditableMediaManifest, WebDataField
from .web_manifest_primitives import (
    CONTINUOUS_ANIMATION_FIELDS,
    WebEasingKind,
    WebEditableField,
    WebInterpolation,
    WebPlaybackMode,
)
from .web_package_paths import local_package_path


class WebEasing(DomainModel):
    kind: WebEasingKind = "linear"
    x1: float = Field(default=0.25, ge=0, le=1)
    y1: float = 0.1
    x2: float = Field(default=0.25, ge=0, le=1)
    y2: float = 1.0


class WebKeyframe(DomainModel):
    time_ms: int = Field(ge=0)
    value: JsonValue
    easing: WebEasing = Field(default_factory=WebEasing)


class WebAnimationTrack(DomainModel):
    field: WebEditableField
    interpolation: WebInterpolation = "continuous"
    keyframes: list[WebKeyframe]

    @model_validator(mode="after")
    def valid_keyframes(self) -> WebAnimationTrack:
        if not self.keyframes:
            raise ValueError("Editable media animation tracks need at least one keyframe")
        times = [item.time_ms for item in self.keyframes]
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("Editable media keyframes must have unique ascending times")
        if self.interpolation == "continuous":
            if self.field not in CONTINUOUS_ANIMATION_FIELDS:
                raise ValueError(f"Field {self.field} only supports discrete keyframes")
            if any(
                not isinstance(item.value, (int, float)) or isinstance(item.value, bool)
                for item in self.keyframes
            ):
                raise ValueError("Continuous keyframe values must be numeric")
        return self


class WebParameterAnimationTrack(DomainModel):
    parameter_id: str
    interpolation: WebInterpolation = "continuous"
    keyframes: list[WebKeyframe]

    @model_validator(mode="after")
    def valid_keyframes(self) -> WebParameterAnimationTrack:
        if not self.keyframes:
            raise ValueError("Editable parameter tracks need at least one keyframe")
        times = [item.time_ms for item in self.keyframes]
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("Editable parameter keyframes need unique ascending times")
        if self.interpolation == "continuous" and any(
            not isinstance(item.value, (int, float)) or isinstance(item.value, bool)
            for item in self.keyframes
        ):
            raise ValueError("Continuous parameter keyframes must be numeric")
        return self


class WebLayerOverride(DomainModel):
    content: str | None = None
    color: str | None = None
    font_family: str | None = None
    font_size: float | None = Field(default=None, gt=0)
    image: str | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    rotation: float | None = None
    opacity: float | None = Field(default=None, ge=0, le=1)
    z_index: int | None = None
    visible: bool | None = None
    enter_ms: int | None = Field(default=None, ge=0)
    exit_ms: int | None = Field(default=None, ge=0)
    delay_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)

    @field_validator("image")
    @classmethod
    def local_image(cls, value: str | None) -> str | None:
        return local_package_path(value) if value is not None else None

    @model_validator(mode="after")
    def ordered_visibility_time(self) -> WebLayerOverride:
        if self.enter_ms is not None and self.exit_ms is not None and self.exit_ms < self.enter_ms:
            raise ValueError("Layer exit time cannot precede its enter time")
        return self

    def changed_fields(self) -> set[str]:
        return set(self.model_dump(exclude_none=True))


class WebDataSnapshot(DomainModel):
    source_kind: Literal["inline", "file", "api"] = "inline"
    source_label: str = ""
    captured_at: int = Field(default_factory=now_ms)
    values: dict[str, JsonValue] = Field(default_factory=dict)
    content_hash: str = ""

    @model_validator(mode="after")
    def fill_content_hash(self) -> WebDataSnapshot:
        payload = json.dumps(
            self.values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        expected = hashlib.sha256(payload).hexdigest()
        if self.content_hash and self.content_hash != expected:
            raise ValueError("Editable media data snapshot hash does not match its values")
        object.__setattr__(self, "content_hash", expected)
        return self


class WebSceneState(DomainModel):
    layers: dict[str, WebLayerOverride] = Field(default_factory=dict)
    animations: dict[str, dict[WebEditableField, WebAnimationTrack]] = Field(default_factory=dict)
    parameters: dict[str, str | float | int | bool] = Field(default_factory=dict)
    parameter_animations: dict[str, WebParameterAnimationTrack] = Field(default_factory=dict)
    parameter_locks: tuple[str, ...] = ()
    data_snapshot: WebDataSnapshot = Field(default_factory=WebDataSnapshot)
    locks: dict[str, tuple[WebEditableField, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_state(self) -> WebSceneState:
        for layer_id, tracks in self.animations.items():
            for field, track in tracks.items():
                if field != track.field:
                    raise ValueError(f"Animation track key does not match its field: {layer_id}/{field}")
        for layer_id, fields in self.locks.items():
            if len(set(fields)) != len(fields):
                raise ValueError(f"Locked fields must be unique: {layer_id}")
        for parameter_id, parameter_track in self.parameter_animations.items():
            if parameter_id != parameter_track.parameter_id:
                raise ValueError(f"Parameter track key does not match its id: {parameter_id}")
        if len(set(self.parameter_locks)) != len(self.parameter_locks):
            raise ValueError("Scene parameter locks must be unique")
        return self


class WebRuntimeVariant(DomainModel):
    id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class WebRuntimePlayback(DomainModel):
    mode: WebPlaybackMode


class WebClipState(DomainModel):
    clip_id: str
    scenes: dict[str, WebSceneState] = Field(default_factory=dict)
    theme: dict[str, str | float] = Field(default_factory=dict)
    parameters: dict[str, str | float | int | bool] = Field(default_factory=dict)
    parameter_locks: tuple[str, ...] = ()
    variant: WebRuntimeVariant | None = None
    scene_id: str | None = None
    playback: WebRuntimePlayback | None = None
    source_hash: str = ""
    batch_name: str = ""
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def coherent_state(self) -> WebClipState:
        if any(not scene_id.strip() for scene_id in self.scenes):
            raise ValueError("Editable media scene state identifiers cannot be empty")
        if len(set(self.parameter_locks)) != len(self.parameter_locks):
            raise ValueError("Global parameter locks must be unique")
        return self


class WebStateDiff(DomainModel):
    clip_id: str
    before_revision: int
    changes: dict[str, dict[Literal["before", "after"], JsonValue]] = Field(default_factory=dict)
    locked_paths: list[str] = Field(default_factory=list)


class WebEditDocument(DomainModel):
    clip_id: str
    scene_id: str
    variant_id: str
    revision: int
    scene_duration_ms: int
    fields: list[EditorFieldValue]


class WebRebindConflict(DomainModel):
    path: str
    kind: Literal[
        "removed-layer",
        "removed-field",
        "removed-scene",
        "removed-parameter",
        "incompatible-value",
        "out-of-range-keyframe",
        "removed-data-field",
        "removed-theme-variable",
        "removed-variant",
        "removed-media-source",
    ]
    message: str
    current_value: JsonValue = None
    allowed_resolutions: tuple[Literal["drop", "default"], ...]


class WebRebindPlan(DomainModel):
    asset_id: str
    old_source_hash: str
    new_source_hash: str
    plan_digest: str
    retained_layers: list[str] = Field(default_factory=list)
    added_layers: list[str] = Field(default_factory=list)
    removed_layers: list[str] = Field(default_factory=list)
    affected_clips: list[str] = Field(default_factory=list)
    conflicts: list[WebRebindConflict] = Field(default_factory=list)


class WebRebindCommitReport(DomainModel):
    asset_id: str
    old_source_hash: str
    new_source_hash: str
    plan_digest: str
    migrated_clips: list[str] = Field(default_factory=list)
    resolved_paths: dict[str, Literal["drop", "default"]] = Field(default_factory=dict)
    archive_path: str = ""


class WebVariantResult(DomainModel):
    sequence_id: str
    clip_id: str
    name: str
    revision: int


def resolved_web_scene_data(
    state: WebClipState,
    manifest: EditableMediaManifest,
    scene_id: str,
) -> dict[str, JsonValue]:
    try:
        scene = next(item for item in manifest.scenes if item.id == scene_id)
    except StopIteration as error:
        raise ValueError(f"Editable media scene does not exist: {scene_id}") from error
    current = state.scenes.get(scene.id, WebSceneState())
    data = {item.id: item.default for item in manifest.data_fields}
    data.update(scene.data)
    data.update(current.data_snapshot.values)
    return data


def media_source_ids_in_web_data(
    data: Mapping[str, JsonValue],
    fields: list[WebDataField],
) -> tuple[str, ...]:
    source_ids: list[str] = []
    for field in fields:
        value = data.get(field.id)
        if field.kind == "media-source":
            if isinstance(value, str) and value:
                source_ids.append(value)
            continue
        if field.kind != "table" or not isinstance(value, list):
            continue
        source_columns = {column.id for column in field.columns if column.kind == "media-source"}
        for row in value:
            if not isinstance(row, dict):
                continue
            for column_id in source_columns:
                source_id = row.get(column_id)
                if isinstance(source_id, str) and source_id:
                    source_ids.append(source_id)
    return tuple(dict.fromkeys(source_ids))


def web_runtime_state(
    state: WebClipState,
    manifest: EditableMediaManifest,
) -> dict[str, JsonValue]:
    variant = manifest.variant_for(state.variant.id if state.variant is not None else None)
    known_scene_ids = {item.id for item in manifest.scenes}
    scene_id = state.scene_id if state.scene_id in known_scene_ids else manifest.scenes[0].id
    resolved_scenes: dict[str, JsonValue] = {}
    for scene in manifest.scenes:
        current = state.scenes.get(scene.id, WebSceneState())
        resolved_layers: dict[str, JsonValue] = {}
        for layer in manifest.layers:
            values = manifest.layer_values_for(variant.id, layer.id)
            values.update(current.layers.get(layer.id, WebLayerOverride()).model_dump(exclude_none=True))
            resolved_layers[layer.id] = values
        data = resolved_web_scene_data(state, manifest, scene.id)
        scene_parameters = {
            item.descriptor.id: item.descriptor.default
            for item in manifest.parameters
            if item.binding.scope == "scene"
        }
        scene_parameters.update(scene.parameters)
        scene_parameters.update(current.parameters)
        resolved_scenes[scene.id] = cast(
            JsonValue,
            {
                "layers": resolved_layers,
                "animations": {
                    layer_id: {field: track.model_dump(mode="json") for field, track in tracks.items()}
                    for layer_id, tracks in current.animations.items()
                },
                "parameters": cast(JsonValue, scene_parameters),
                "parameter_animations": {
                    parameter_id: track.model_dump(mode="json")
                    for parameter_id, track in current.parameter_animations.items()
                },
                "parameter_locks": list(current.parameter_locks),
                "data": data,
                "locks": {layer_id: list(fields) for layer_id, fields in current.locks.items()},
            },
        )
    theme = {item.id: item.default for item in manifest.theme_variables}
    theme.update(state.theme)
    parameters = {
        item.descriptor.id: item.descriptor.default
        for item in manifest.parameters
        if item.binding.scope == "global"
    }
    parameters.update(state.parameters)
    return {
        "scenes": cast(JsonValue, resolved_scenes),
        "theme": cast(JsonValue, theme),
        "theme_bindings": cast(JsonValue, {item.id: item.css_variable for item in manifest.theme_variables}),
        "parameters": cast(JsonValue, parameters),
        "parameter_bindings": cast(
            JsonValue,
            {
                item.descriptor.id: item.binding.css_variable
                for item in manifest.parameters
                if item.binding.css_variable is not None
            },
        ),
        "parameter_locks": list(state.parameter_locks),
        "variant": cast(
            JsonValue,
            {
                "id": variant.id,
                "width": variant.canvas.width,
                "height": variant.canvas.height,
            },
        ),
        "scene_id": scene_id,
        "playback": cast(
            JsonValue,
            {
                "mode": state.playback.mode if state.playback is not None else manifest.playback.mode,
            },
        ),
        "revision": state.revision,
    }
