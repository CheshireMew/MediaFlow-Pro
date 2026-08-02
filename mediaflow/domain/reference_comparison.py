from __future__ import annotations

from typing import Literal

from pydantic import Field

from mediaflow.domain.model_base import DomainModel


class ReferenceComparisonAcceptance(DomainModel):
    require_same_remaining_frame_count: bool = True
    minimum_exact_frame_ratio: float | None = Field(default=None, ge=0, le=1)
    maximum_mean_absolute_error: float | None = Field(default=None, ge=0)
    maximum_boundary_mean_absolute_error: float | None = Field(default=None, ge=0)
    minimum_psnr_db: float | None = Field(default=None, ge=0)
    maximum_temporal_mismatch_count: int | None = Field(default=None, ge=0)


class ComparedMediaIdentity(DomainModel):
    path: str
    sha256: str = Field(pattern="^[a-f0-9]{64}$")
    codec: str
    pixel_format: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_rate_numerator: int = Field(gt=0)
    frame_rate_denominator: int = Field(gt=0)
    total_frame_count: int = Field(gt=0)
    duration_seconds: float = Field(ge=0)
    selected_start_frame: int = Field(ge=0)
    selected_frame_count: int = Field(gt=0)
    remaining_frame_count: int = Field(gt=0)


class ReferenceComparisonSummary(DomainModel):
    compared_frame_count: int = Field(gt=0)
    frame_count_delta: int
    exact_frame_count: int = Field(ge=0)
    exact_frame_ratio: float = Field(ge=0, le=1)
    mean_absolute_error: float = Field(ge=0)
    maximum_mean_absolute_error: float = Field(ge=0)
    maximum_mean_absolute_error_frame: int = Field(ge=0)
    minimum_psnr_db: float | None = Field(default=None, ge=0)
    minimum_psnr_frame: int | None = Field(default=None, ge=0)
    maximum_boundary_mean_absolute_error: float = Field(ge=0)
    temporal_search_radius_frames: int = Field(ge=0)
    temporal_mismatch_count: int = Field(ge=0)
    maximum_temporal_offset_frames: int = Field(ge=0)


class ReferenceComparisonArtifact(DomainModel):
    path: str
    sha256: str = Field(pattern="^[a-f0-9]{64}$")
    bytes: int = Field(gt=0)


class ReferenceComparisonArtifacts(DomainModel):
    report: ReferenceComparisonArtifact
    contact_sheet: ReferenceComparisonArtifact
    worst_frame: ReferenceComparisonArtifact


class ReferenceComparisonResult(DomainModel):
    protocol: Literal["mediaflow-reference-comparison"] = (
        "mediaflow-reference-comparison"
    )
    version: Literal[1] = 1
    status: Literal["measured", "passed", "failed"]
    reference: ComparedMediaIdentity
    candidate: ComparedMediaIdentity
    summary: ReferenceComparisonSummary
    acceptance: ReferenceComparisonAcceptance | None = None
    acceptance_failures: list[str]
    artifacts: ReferenceComparisonArtifacts
