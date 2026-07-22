from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal, cast

from pydantic import Field, JsonValue, field_validator, model_validator

from .model_base import DomainModel, now_ms

WebLayerKind = Literal["text", "image", "shape", "group", "component"]
WebEditableField = Literal[
    "content",
    "color",
    "font_family",
    "font_size",
    "image",
    "x",
    "y",
    "width",
    "height",
    "rotation",
    "opacity",
    "z_index",
    "visible",
    "enter_ms",
    "exit_ms",
    "delay_ms",
    "duration_ms",
]
WebLayoutField = Literal["x", "y", "width", "height", "rotation"]
WebDataKind = Literal["string", "number", "boolean", "date", "image", "table", "json"]
WebThemeKind = Literal["color", "font", "number", "string"]
WebInterpolation = Literal["continuous", "discrete"]
WebEasingKind = Literal["linear", "ease_in", "ease_out", "ease_in_out", "step", "cubic_bezier"]
WebExportFormat = Literal["png", "gif", "alpha_video", "video", "overlay"]

LAYOUT_FIELDS: frozenset[str] = frozenset({"x", "y", "width", "height", "rotation"})
CONTINUOUS_ANIMATION_FIELDS: frozenset[str] = frozenset(
    {"font_size", "x", "y", "width", "height", "rotation", "opacity", "z_index"}
)


def _local_package_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("Editable media paths must stay inside the package")
    if ":" in path.parts[0]:
        raise ValueError("Editable media paths cannot use a URL or drive protocol")
    return path.as_posix()


class WebCanvas(DomainModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    background_mode: Literal["transparent", "opaque"] = "transparent"
    background_color: str = "#000000"


class WebTimeline(DomainModel):
    duration_ms: int = Field(ge=0)
    fps: float = Field(default=30.0, gt=0, le=240)
    loop: Literal["none", "repeat"] = "none"


class WebLayerBounds(DomainModel):
    x: float = 0.0
    y: float = 0.0
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: float = 0.0


class WebFieldConstraint(DomainModel):
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = Field(default=None, gt=0)
    choices: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_range(self) -> WebFieldConstraint:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Editable field minimum cannot exceed maximum")
        return self


class WebComponentMetadata(DomainModel):
    id: str
    name: str
    category: str = "general"
    tags: list[str] = Field(default_factory=list)

    @field_validator("id", "name", "category")
    @classmethod
    def non_empty_component_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media component metadata cannot be empty")
        return value


class WebThemeVariable(DomainModel):
    id: str
    name: str
    kind: WebThemeKind
    css_variable: str
    default: str | float
    constraints: WebFieldConstraint | None = None

    @field_validator("id", "name", "css_variable")
    @classmethod
    def non_empty_theme_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media theme fields cannot be empty")
        return value

    @field_validator("css_variable")
    @classmethod
    def valid_css_variable(cls, value: str) -> str:
        if not value.startswith("--"):
            raise ValueError("Theme CSS variables must start with --")
        return value

    @model_validator(mode="after")
    def value_matches_kind(self) -> WebThemeVariable:
        if self.kind == "number" and not isinstance(self.default, (int, float)):
            raise ValueError("Number theme defaults must be numeric")
        if self.kind != "number" and not isinstance(self.default, str):
            raise ValueError("Text theme defaults must be strings")
        return self


class WebLayout(DomainModel):
    id: str
    name: str
    canvas: WebCanvas
    layers: dict[str, WebLayerBounds] = Field(default_factory=dict)

    @field_validator("id", "name")
    @classmethod
    def non_empty_layout_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media layout fields cannot be empty")
        return value


class WebDataColumn(DomainModel):
    id: str
    name: str
    kind: Literal["string", "number", "boolean", "date", "image"] = "string"


class WebDataField(DomainModel):
    id: str
    name: str
    kind: WebDataKind
    default: JsonValue = None
    columns: list[WebDataColumn] = Field(default_factory=list)

    @field_validator("id", "name")
    @classmethod
    def non_empty_data_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media data fields cannot be empty")
        return value

    @model_validator(mode="after")
    def table_columns_only(self) -> WebDataField:
        if self.columns and self.kind != "table":
            raise ValueError("Only table data fields can declare columns")
        if len({column.id for column in self.columns}) != len(self.columns):
            raise ValueError("Editable media table columns must be unique")
        if self.default is None:
            return self
        matches = {
            "string": isinstance(self.default, str),
            "date": isinstance(self.default, str),
            "image": isinstance(self.default, str),
            "number": isinstance(self.default, (int, float))
            and not isinstance(self.default, bool),
            "boolean": isinstance(self.default, bool),
            "table": isinstance(self.default, list)
            and all(isinstance(row, dict) for row in self.default),
            "json": True,
        }[self.kind]
        if not matches:
            raise ValueError(f"Data field {self.id} default does not match kind {self.kind}")
        if self.kind == "table" and self.columns:
            if not isinstance(self.default, list):
                raise ValueError(f"Data field {self.id} table default must be a list")
            columns = {column.id: column.kind for column in self.columns}
            for index, row in enumerate(self.default):
                if not isinstance(row, dict):
                    raise ValueError(f"Data field {self.id} default row {index} must be an object")
                if set(row) != set(columns):
                    raise ValueError(
                        f"Data field {self.id} default row {index} columns do not match"
                    )
                for column_id, column_kind in columns.items():
                    value = row[column_id]
                    valid = {
                        "string": isinstance(value, str),
                        "date": isinstance(value, str),
                        "image": isinstance(value, str),
                        "number": isinstance(value, (int, float))
                        and not isinstance(value, bool),
                        "boolean": isinstance(value, bool),
                    }[column_kind]
                    if not valid:
                        raise ValueError(
                            f"Data field {self.id} default row {index} column "
                            f"{column_id} does not match kind {column_kind}"
                        )
        return self


class WebLayerManifest(DomainModel):
    id: str
    name: str
    kind: WebLayerKind
    selector: str
    parent_id: str | None = None
    default_bounds: WebLayerBounds
    editable: tuple[WebEditableField, ...] = ()
    constraints: dict[WebEditableField, WebFieldConstraint] = Field(default_factory=dict)

    @field_validator("id", "name", "selector")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable layer identifiers, names, and selectors cannot be empty")
        return value

    @model_validator(mode="after")
    def constraints_are_editable(self) -> WebLayerManifest:
        unknown = set(self.constraints) - set(self.editable)
        if unknown:
            raise ValueError(f"Constraints reference non-editable fields: {sorted(unknown)}")
        if len(set(self.editable)) != len(self.editable):
            raise ValueError("Editable layer fields must be unique")
        return self


class EditableMediaManifest(DomainModel):
    protocol: Literal["editable-media"] = "editable-media"
    version: Literal[1] = 1
    entry: str
    canvas: WebCanvas
    timeline: WebTimeline
    layers: list[WebLayerManifest]
    resources: list[str] = Field(default_factory=list)
    component: WebComponentMetadata | None = None
    theme_variables: list[WebThemeVariable] = Field(default_factory=list)
    layouts: list[WebLayout] = Field(default_factory=list)
    default_layout_id: str = "default"
    data_fields: list[WebDataField] = Field(default_factory=list)

    @field_validator("entry")
    @classmethod
    def local_entry(cls, value: str) -> str:
        return _local_package_path(value)

    @field_validator("resources")
    @classmethod
    def local_resources(cls, values: list[str]) -> list[str]:
        normalized = [_local_package_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Editable media resources must be unique")
        return normalized

    @model_validator(mode="after")
    def valid_layer_tree(self) -> EditableMediaManifest:
        ids = [layer.id for layer in self.layers]
        if not ids:
            raise ValueError("Editable media must declare at least one editable layer")
        if len(set(ids)) != len(ids):
            raise ValueError("Editable layer identifiers must be unique")
        known = set(ids)
        for layer in self.layers:
            if layer.parent_id == layer.id:
                raise ValueError(f"Editable layer cannot be its own parent: {layer.id}")
            if layer.parent_id is not None and layer.parent_id not in known:
                raise ValueError(f"Editable layer parent does not exist: {layer.parent_id}")
        for layer in self.layers:
            visited = {layer.id}
            parent_id = layer.parent_id
            while parent_id is not None:
                if parent_id in visited:
                    raise ValueError("Editable layer groups cannot contain a cycle")
                visited.add(parent_id)
                parent_id = next(item.parent_id for item in self.layers if item.id == parent_id)
        if len({item.id for item in self.layouts}) != len(self.layouts):
            raise ValueError("Editable media layout identifiers must be unique")
        if self.layouts and self.default_layout_id not in {item.id for item in self.layouts}:
            raise ValueError("Editable media default layout does not exist")
        for layout in self.layouts:
            unknown_layers = set(layout.layers) - known
            if unknown_layers:
                raise ValueError(
                    f"Layout {layout.id} references unknown layers: {sorted(unknown_layers)}"
                )
        if len({item.id for item in self.theme_variables}) != len(self.theme_variables):
            raise ValueError("Editable media theme variable identifiers must be unique")
        if len({item.css_variable for item in self.theme_variables}) != len(self.theme_variables):
            raise ValueError("Editable media theme CSS variables must be unique")
        if len({item.id for item in self.data_fields}) != len(self.data_fields):
            raise ValueError("Editable media data field identifiers must be unique")
        return self

    def layout_for(self, layout_id: str | None, width: int, height: int) -> WebLayout:
        if not self.layouts:
            return WebLayout(
                id="default",
                name="Default",
                canvas=self.canvas,
                layers={layer.id: layer.default_bounds for layer in self.layers},
            )
        if layout_id:
            try:
                return next(item for item in self.layouts if item.id == layout_id)
            except StopIteration as error:
                raise ValueError(f"Editable media layout does not exist: {layout_id}") from error
        target_ratio = width / max(1, height)
        return min(
            self.layouts,
            key=lambda item: abs(item.canvas.width / item.canvas.height - target_ratio),
        )


class WebAssetSpec(DomainModel):
    asset_id: str
    manifest: EditableMediaManifest
    source_hash: str


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
        return _local_package_path(value) if value is not None else None

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


class WebClipState(DomainModel):
    clip_id: str
    layers: dict[str, WebLayerOverride] = Field(default_factory=dict)
    layout_id: str | None = None
    layout_overrides: dict[str, dict[str, WebLayerOverride]] = Field(default_factory=dict)
    animations: dict[str, dict[WebEditableField, WebAnimationTrack]] = Field(default_factory=dict)
    theme: dict[str, str | float] = Field(default_factory=dict)
    data_snapshot: WebDataSnapshot = Field(default_factory=WebDataSnapshot)
    locks: dict[str, tuple[WebEditableField, ...]] = Field(default_factory=dict)
    source_hash: str = ""
    variant_name: str = ""
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def coherent_state(self) -> WebClipState:
        for layout_id, layers in self.layout_overrides.items():
            if not layout_id.strip():
                raise ValueError("Editable media layout override IDs cannot be empty")
            for layer_id, override in layers.items():
                unsupported = override.changed_fields() - LAYOUT_FIELDS
                if unsupported:
                    raise ValueError(
                        f"Layout override {layout_id}/{layer_id} contains non-layout fields: "
                        f"{sorted(unsupported)}"
                    )
        for layer_id, tracks in self.animations.items():
            for field, track in tracks.items():
                if field != track.field:
                    raise ValueError(f"Animation track key does not match its field: {layer_id}/{field}")
        for layer_id, fields in self.locks.items():
            if len(set(fields)) != len(fields):
                raise ValueError(f"Locked fields must be unique: {layer_id}")
        return self


class WebStateDiff(DomainModel):
    clip_id: str
    before_revision: int
    changes: dict[str, dict[Literal["before", "after"], JsonValue]] = Field(
        default_factory=dict
    )
    locked_paths: list[str] = Field(default_factory=list)


class WebRebindReport(DomainModel):
    asset_id: str
    old_source_hash: str
    new_source_hash: str
    retained_layers: list[str] = Field(default_factory=list)
    added_layers: list[str] = Field(default_factory=list)
    removed_layers: list[str] = Field(default_factory=list)
    affected_clips: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    archive_path: str = ""


class WebComponentRecord(DomainModel):
    component_id: str
    name: str
    category: str
    tags: list[str] = Field(default_factory=list)
    version_hash: str
    package_path: str


class WebVariantResult(DomainModel):
    sequence_id: str
    clip_id: str
    name: str
    revision: int


class WebClipExportResult(DomainModel):
    clip_id: str
    format: WebExportFormat
    output_path: str
    cache_path: str


def web_runtime_state(
    state: WebClipState,
    manifest: EditableMediaManifest,
    *,
    width: int,
    height: int,
) -> dict[str, JsonValue]:
    layout = manifest.layout_for(state.layout_id, width, height)
    resolved_layers: dict[str, JsonValue] = {}
    layout_changes = state.layout_overrides.get(layout.id, {})
    for layer in manifest.layers:
        bounds = layout.layers.get(layer.id, layer.default_bounds)
        values: dict[str, JsonValue] = bounds.model_dump(mode="json")
        values.update(state.layers.get(layer.id, WebLayerOverride()).model_dump(exclude_none=True))
        values.update(layout_changes.get(layer.id, WebLayerOverride()).model_dump(exclude_none=True))
        resolved_layers[layer.id] = values
    theme = {item.id: item.default for item in manifest.theme_variables}
    theme.update(state.theme)
    data = {item.id: item.default for item in manifest.data_fields}
    data.update(state.data_snapshot.values)
    return {
        "layers": resolved_layers,
        "animations": cast(JsonValue, {
            layer_id: {
                field: track.model_dump(mode="json") for field, track in tracks.items()
            }
            for layer_id, tracks in state.animations.items()
        }),
        "theme": cast(JsonValue, theme),
        "theme_bindings": cast(JsonValue, {
            item.id: item.css_variable for item in manifest.theme_variables
        }),
        "data": cast(JsonValue, data),
        "layout": cast(JsonValue, {
            "id": layout.id,
            "width": layout.canvas.width,
            "height": layout.canvas.height,
            "background_mode": layout.canvas.background_mode,
            "background_color": layout.canvas.background_color,
        }),
        "locks": cast(
            JsonValue,
            {layer_id: list(fields) for layer_id, fields in state.locks.items()},
        ),
        "revision": state.revision,
    }
