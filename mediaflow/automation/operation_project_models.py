from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from mediaflow.domain.collaboration import ProjectChangeEvent
from mediaflow.domain.enums import (
    ColorMode,
)
from mediaflow.domain.media_resources import (
    MediaResourceCatalogItem,
    MediaResourceCategory,
)
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.project import Asset, Project, ProjectProfile, Sequence
from mediaflow.domain.project_archive import ProjectArchiveResult
from mediaflow.domain.project_collection import (
    ProjectCollectionPreview,
    ProjectCollectionRecord,
    ProjectCollectionResult,
)
from mediaflow.domain.project_records import ExportHistoryRecord, ProjectVersionRecord
from mediaflow.domain.reference_comparison import (
    ReferenceComparisonAcceptance,
    ReferenceComparisonResult,
)
from mediaflow.domain.runtime_capabilities import RuntimeInspection
from mediaflow.domain.sequence_variants import (
    SequenceVariantGeneration,
    SequenceVariantPlan,
    SequenceVariantRecord,
    SequenceVariantSpec,
)
from mediaflow.domain.tasks import Task
from mediaflow.domain.timeline import (
    TimelineState,
)
from mediaflow.domain.transcript_edits import (
    TranscriptSnapshot,
)
from mediaflow.domain.workflows import WorkflowRun

from .operation_model_common import PublicWebAssetSpec


class MediaResourceSearchArguments(DomainModel):
    color_mode: ColorMode = ColorMode.SDR_BT709
    catalog_paths: list[str] | None = None
    category: MediaResourceCategory | None = None
    query: str = ""
    tags: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class ReferenceComparisonArguments(DomainModel):
    reference_path: str = Field(min_length=1)
    candidate_path: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)
    reference_start_frame: int = Field(default=0, ge=0)
    candidate_start_frame: int = Field(default=0, ge=0)
    frame_count: int | None = Field(default=None, gt=0)
    temporal_search_radius_frames: int = Field(default=0, ge=0, le=5)
    boundary_frame_count: int = Field(default=3, gt=0, le=30)
    contact_sheet_rows: int = Field(default=8, gt=0, le=20)
    acceptance: ReferenceComparisonAcceptance | None = None
    overwrite: bool = False


ReferenceComparisonOperationResult = ReferenceComparisonResult


class ProjectCreateArguments(DomainModel):
    name: str = Field(min_length=1)
    directory_name: str = Field(min_length=1)
    profile: ProjectProfile


class ProjectVersionCreateArguments(DomainModel):
    name: str = Field(min_length=1)


class ProjectVersionRestoreArguments(DomainModel):
    version_id: str = Field(min_length=1)


class ProjectCollectionArguments(DomainModel):
    asset_ids: list[str] | None = None

    @model_validator(mode="after")
    def unique_asset_ids(self) -> ProjectCollectionArguments:
        if self.asset_ids is not None:
            if not self.asset_ids:
                raise ValueError("asset_ids cannot be empty when provided")
            if len(self.asset_ids) != len(set(self.asset_ids)):
                raise ValueError("asset_ids must be unique")
        return self


class ProjectCollectionStateArguments(DomainModel):
    collection_id: str = Field(min_length=1)
    using_collected: bool


class ProjectCollectionListResult(DomainModel):
    collections: list[ProjectCollectionRecord]


ProjectCollectionPreviewResult = ProjectCollectionPreview
ProjectCollectionApplyResult = ProjectCollectionResult


class ProjectArchiveCreateArguments(DomainModel):
    destination: str = Field(min_length=1)

    @model_validator(mode="after")
    def absolute_destination(self) -> ProjectArchiveCreateArguments:
        if not Path(self.destination).is_absolute():
            raise ValueError("destination must be absolute")
        return self


ProjectArchiveCreateResult = ProjectArchiveResult


class ProjectChangesListArguments(DomainModel):
    since_revision: int = Field(ge=0)
    actor_kind: Literal["human", "agent", "automation", "system"] | None = None


class ProjectHandoffInspectArguments(DomainModel):
    version_id: str | None = None
    sequence_id: str | None = None


class ProjectContextInspectArguments(ProjectHandoffInspectArguments):
    document_id: str | None = None
    include_transcript: bool = True


class AssetImportArguments(DomainModel):
    source: str = Field(min_length=1)
    timeout: float | None = Field(default=None, gt=0)


class SequenceShortCreateArguments(DomainModel):
    source_sequence_id: str = Field(min_length=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    name: str | None = None

    @model_validator(mode="after")
    def positive_range(self) -> SequenceShortCreateArguments:
        if self.end_frame <= self.start_frame:
            raise ValueError("end_frame must be after start_frame")
        return self


class SequenceVariantListArguments(DomainModel):
    source_sequence_id: str | None = None
    include_archived: bool = False


class SequenceVariantGenerateArguments(DomainModel):
    source_sequence_id: str = Field(min_length=1)
    specs: list[SequenceVariantSpec] = Field(min_length=1, max_length=8)
    force: bool = False
    conflict_resolutions: dict[str, Literal["keep_local", "take_source"]] = Field(
        default_factory=dict
    )


class SequenceVariantPlanArguments(DomainModel):
    source_sequence_id: str = Field(min_length=1)
    specs: list[SequenceVariantSpec] = Field(min_length=1, max_length=8)


class SequenceVariantStatus(DomainModel):
    record: SequenceVariantRecord
    sequence: Sequence
    stale: bool


class SequenceVariantListResult(DomainModel):
    variants: list[SequenceVariantStatus]


SequenceVariantGenerateResult = SequenceVariantGeneration
SequenceVariantPlanResult = SequenceVariantPlan


class DiagnosticsBundleArguments(DomainModel):
    output_path: str = Field(min_length=1)
    task_ids: list[str] = Field(default_factory=list)
    overwrite: bool = False

    @model_validator(mode="after")
    def valid_output(self) -> DiagnosticsBundleArguments:
        output = Path(self.output_path)
        if not output.is_absolute():
            raise ValueError("output_path must be absolute")
        if output.suffix.lower() != ".zip":
            raise ValueError("output_path must use the .zip extension")
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("task_ids must be unique")
        return self


class ProjectSnapshotResult(DomainModel):
    project: Project
    path: str
    read_only: bool
    sequences: list[Sequence]
    assets: list[Asset]
    web_assets: list[PublicWebAssetSpec]
    active_workflows: list[WorkflowRun]
    tasks: list[Task]


class ProjectUpgradeResult(ProjectSnapshotResult):
    upgraded: Literal[True]


class ProjectVersionListResult(DomainModel):
    versions: list[ProjectVersionRecord]


class ProjectVersionResult(DomainModel):
    version: ProjectVersionRecord


class ProjectVersionRestoreResult(ProjectSnapshotResult):
    restored_version: ProjectVersionRecord


class ProjectChangeSummary(DomainModel):
    cursor: int = Field(ge=1)
    project_revision: int = Field(ge=0)
    actor_kind: Literal["human", "agent", "automation", "system"]
    actor_name: str
    operation: str
    summary: str
    paths: list[str]


class ProjectChangesListResult(DomainModel):
    since_revision: int = Field(ge=0)
    current_revision: int = Field(ge=0)
    events: list[ProjectChangeEvent]
    summaries: list[ProjectChangeSummary]


class ProjectHandoffInspectResult(ProjectChangesListResult):
    project_id: str
    project_path: str
    anchor_version: ProjectVersionRecord | None
    offline_asset_ids: list[str]
    latest_export: ExportHistoryRecord | None
    export_matches_current_revision: bool
    ready_for_handoff: bool


class ProjectContextInspectResult(DomainModel):
    content_revision: int = Field(ge=0)
    project: Project
    path: str
    read_only: bool
    sequence: Sequence
    timeline: TimelineState
    transcript: TranscriptSnapshot | None
    transcript_error: str | None
    handoff: ProjectHandoffInspectResult


class MediaResourceCatalogSourceResult(DomainModel):
    catalog_id: str | None
    catalog_version: str | None
    catalog_path: str | None
    item_count: int = Field(ge=0)
    error: str | None


class MediaResourceEntryResult(MediaResourceCatalogItem):
    resource_key: str
    catalog_id: str
    catalog_version: str
    catalog_path: str | None
    preview_path: str
    adoption_path: str


class MediaResourceSearchResult(DomainModel):
    sources: list[MediaResourceCatalogSourceResult]
    categories: list[MediaResourceCategory]
    tags: list[str]
    featured_count: int = Field(ge=0)
    result_count: int = Field(ge=0)
    items: list[MediaResourceEntryResult]


class AssetListResult(DomainModel):
    assets: list[Asset]


class SequenceResult(DomainModel):
    sequence: Sequence


RuntimeInspectionResult = RuntimeInspection
