from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.project import Asset
from mediaflow.domain.web_exports import WebExportFormat
from mediaflow.domain.web_manifest_primitives import WebEditableField
from mediaflow.domain.web_rendering import WebRenderPlan
from mediaflow.domain.web_state import (
    WebClipState,
    WebEasing,
    WebEditDocument,
    WebRebindCommitReport,
    WebRebindPlan,
    WebStateDiff,
    WebVariantResult,
)

from .operation_model_common import Actor, PublicWebAssetSpec


class WebImportArguments(DomainModel):
    source: str = Field(min_length=1)


class WebInspectArguments(DomainModel):
    asset_id: str = Field(min_length=1)


class WebClipGetArguments(DomainModel):
    clip_id: str = Field(min_length=1)


class WebClipEditDescribeArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str | None = None


class WebClipUpdateArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    updates: dict[str, JsonValue]
    expected_revision: int | None = Field(default=None, ge=0)
    actor: Actor | None = None


class WebClipVariantSelectArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    expected_revision: int | None = Field(default=None, ge=0)


class WebClipKeyframeSetArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    layer_id: str = Field(min_length=1)
    field: WebEditableField
    time_ms: int = Field(ge=0)
    value: JsonValue
    easing: WebEasing | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    actor: Actor | None = None


class WebClipKeyframeRemoveArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    layer_id: str = Field(min_length=1)
    field: WebEditableField
    time_ms: int = Field(ge=0)
    expected_revision: int | None = Field(default=None, ge=0)


class WebParameterUpdateArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    parameter_id: str = Field(min_length=1)
    value: JsonValue
    scene_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    actor: Actor | None = None


class WebParameterKeyframeSetArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    parameter_id: str = Field(min_length=1)
    time_ms: int = Field(ge=0)
    value: JsonValue
    easing: WebEasing | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    actor: Actor | None = None


class WebParameterKeyframeRemoveArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    parameter_id: str = Field(min_length=1)
    time_ms: int = Field(ge=0)
    expected_revision: int | None = Field(default=None, ge=0)


class WebParameterLockUpdateArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    parameter_id: str = Field(min_length=1)
    locked: bool
    scene_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)


class WebThemeUpdateArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    changes: dict[str, str | float]
    expected_revision: int | None = Field(default=None, ge=0)


class WebDataUpdateArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    values: dict[str, JsonValue]
    source_kind: Literal["inline", "file", "api"] | None = None
    source_label: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)


class WebDataSnapshotArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    field_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)


class WebFieldLockUpdateArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    layer_id: str = Field(min_length=1)
    fields: list[WebEditableField] = Field(min_length=1)
    locked: bool
    expected_revision: int | None = Field(default=None, ge=0)


class WebClipRenderArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    timeout: float | None = Field(default=None, gt=0)


class WebClipRenderInspectArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)


class WebClipExportArguments(DomainModel):
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    format: WebExportFormat
    time_ms: int | None = Field(default=None, ge=0)
    background: str | None = None
    overwrite: bool | None = None
    timeout: float | None = Field(default=None, gt=0)


class WebBatchCreateArguments(DomainModel):
    source_sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    records: list[dict[str, JsonValue]] | None = None
    source: str | None = None
    bindings: dict[str, str]
    name_template: str | None = None
    actor: Actor | None = None

    @model_validator(mode="after")
    def exactly_one_record_source(self) -> WebBatchCreateArguments:
        if (self.records is None) == (self.source is None):
            raise ValueError("exactly one of records or source is required")
        return self


class WebAssetRebindPlanArguments(DomainModel):
    asset_id: str = Field(min_length=1)
    source: str = Field(min_length=1)


class WebAssetRebindCommitArguments(WebAssetRebindPlanArguments):
    plan_digest: str = Field(min_length=1)
    resolutions: dict[str, Literal["drop", "default"]]


class WebImportResult(DomainModel):
    asset: Asset
    web_asset: PublicWebAssetSpec


class WebInspectResult(DomainModel):
    web_asset: PublicWebAssetSpec


class WebClipStateResult(DomainModel):
    web_clip_state: WebClipState


class WebClipRenderInspectionResult(DomainModel):
    render_plan: WebRenderPlan


class WebEditDocumentResult(DomainModel):
    edit_document: WebEditDocument


class WebStateDiffResult(DomainModel):
    diff: WebStateDiff


class WebBatchResult(DomainModel):
    variants: list[WebVariantResult]


class WebRebindPlanResult(DomainModel):
    rebind_plan: WebRebindPlan


class WebRebindCommitResult(DomainModel):
    rebind_commit: WebRebindCommitReport
