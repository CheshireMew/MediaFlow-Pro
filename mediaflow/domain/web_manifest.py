from __future__ import annotations

import json
from typing import Literal, cast

from pydantic import Field, JsonValue, field_validator, model_validator

from .editable_media_contract import EditableMediaContract
from .model_base import DomainModel
from .web_manifest_primitives import (
    WebAccessibility,
    WebComponentMetadata,
    WebDataKind,
    WebDelivery,
    WebEditableField,
    WebFieldConstraint,
    WebFrameReadiness,
    WebLayerBounds,
    WebLayerKind,
    WebLayoutContract,
    WebParameter,
    WebPlayback,
    WebQuality,
    WebScene,
    WebThemeVariable,
    WebVariant,
)
from .web_manifest_validation import validate_manifest_contract
from .web_package_paths import local_package_path


class WebDataColumn(DomainModel):
    id: str
    name: str
    kind: Literal["string", "number", "boolean", "date", "media-source"] = "string"


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
            "media-source": isinstance(self.default, str),
            "number": isinstance(self.default, (int, float)) and not isinstance(self.default, bool),
            "boolean": isinstance(self.default, bool),
            "list": isinstance(self.default, list),
            "table": isinstance(self.default, list) and all(isinstance(row, dict) for row in self.default),
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
                    raise ValueError(f"Data field {self.id} default row {index} columns do not match")
                for column_id, column_kind in columns.items():
                    value = row[column_id]
                    valid = {
                        "string": isinstance(value, str),
                        "date": isinstance(value, str),
                        "media-source": isinstance(value, str),
                        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
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


class WebProductionMetadata(DomainModel):
    source_id: str | None = Field(default=None, min_length=1)
    source_version: str | None = Field(default=None, min_length=1)
    content_unit_id: str | None = Field(default=None, min_length=1)
    media_project_id: str | None = Field(default=None, min_length=1)
    media_script_version: str | None = Field(default=None, min_length=1)
    profile_id: str | None = Field(default=None, min_length=1)


class EditableMediaManifest(DomainModel):
    protocol: Literal["editable-media"]
    version: Literal[6]
    entry: str
    media_sources: str
    playback: WebPlayback
    frame_readiness: WebFrameReadiness
    accessibility: WebAccessibility
    layers: list[WebLayerManifest]
    component: WebComponentMetadata
    theme_variables: list[WebThemeVariable]
    parameters: list[WebParameter]
    scenes: list[WebScene]
    layout_contracts: list[WebLayoutContract]
    variants: list[WebVariant]
    default_variant_id: str
    data_fields: list[WebDataField]
    quality: WebQuality
    delivery: WebDelivery
    resources: list[str]
    production: WebProductionMetadata | None = None

    @field_validator("entry", "media_sources")
    @classmethod
    def local_manifest_path(cls, value: str) -> str:
        return local_package_path(value)

    @field_validator("resources")
    @classmethod
    def local_resources(cls, values: list[str]) -> list[str]:
        normalized = [local_package_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Editable media resources must be unique")
        return normalized

    @model_validator(mode="after")
    def valid_contract(self) -> EditableMediaManifest:
        validate_manifest_contract(self)
        return self

    @property
    def duration_ms(self) -> int:
        return sum(item.duration_ms for item in self.scenes)

    @property
    def default_variant(self) -> WebVariant:
        return self.variant_for(self.default_variant_id)

    def variant_for(self, variant_id: str | None) -> WebVariant:
        resolved = variant_id or self.default_variant_id
        try:
            return next(item for item in self.variants if item.id == resolved)
        except StopIteration as error:
            raise ValueError(f"Editable media variant does not exist: {resolved}") from error

    def layer_values_for(
        self,
        variant_id: str | None,
        layer_id: str,
    ) -> dict[str, JsonValue]:
        try:
            layer = next(item for item in self.layers if item.id == layer_id)
        except StopIteration as error:
            raise ValueError(f"Editable media layer does not exist: {layer_id}") from error
        values = cast(
            dict[str, JsonValue],
            layer.default_bounds.model_dump(mode="json"),
        )
        variant_layer = self.variant_for(variant_id).layers.get(layer_id)
        if variant_layer is not None:
            values.update(
                cast(
                    dict[str, JsonValue],
                    variant_layer.model_dump(mode="json", exclude_none=True),
                )
            )
        return values

    def parameter_for(self, parameter_id: str) -> WebParameter:
        try:
            return next(item for item in self.parameters if item.descriptor.id == parameter_id)
        except StopIteration as error:
            raise ValueError(f"Editable media parameter does not exist: {parameter_id}") from error


def parse_editable_media_manifest(
    document: object,
    contract: EditableMediaContract,
) -> EditableMediaManifest:
    contract.validate(document)
    return EditableMediaManifest.model_validate(document)


def parse_editable_media_manifest_json(
    value: str,
    contract: EditableMediaContract,
) -> EditableMediaManifest:
    return parse_editable_media_manifest(json.loads(value), contract)


def editable_media_manifest_document(
    manifest: EditableMediaManifest,
) -> dict[str, JsonValue]:
    document = manifest.model_dump(mode="json", exclude_none=True)
    scenes = document.get("scenes")
    if not isinstance(scenes, list):
        raise RuntimeError("Editable media manifest scenes were not serialized")
    for scene in scenes:
        if not isinstance(scene, dict):
            raise RuntimeError("Editable media manifest scene was not serialized")
        motion = scene.get("motion")
        if not isinstance(motion, dict):
            raise RuntimeError("Editable media manifest motion was not serialized")
        motion.setdefault("camera", None)
    parameters = document.get("parameters")
    if not isinstance(parameters, list):
        raise RuntimeError("Editable media manifest parameters were not serialized")
    for parameter in parameters:
        if not isinstance(parameter, dict):
            raise RuntimeError("Editable media parameter was not serialized")
        descriptor = parameter.get("descriptor")
        binding = parameter.get("binding")
        if not isinstance(descriptor, dict) or not isinstance(binding, dict):
            raise RuntimeError("Editable media parameter binding was not serialized")
        descriptor.setdefault("unit", None)
        descriptor.setdefault("options_source", None)
        binding.setdefault("css_variable", None)
    return cast(dict[str, JsonValue], document)


class WebAssetSpec(DomainModel):
    asset_id: str
    manifest: EditableMediaManifest
    source_hash: str


def web_asset_spec_document(spec: WebAssetSpec) -> dict[str, JsonValue]:
    return {
        "asset_id": spec.asset_id,
        "manifest": editable_media_manifest_document(spec.manifest),
        "source_hash": spec.source_hash,
    }
