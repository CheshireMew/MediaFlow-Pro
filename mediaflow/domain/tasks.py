from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, computed_field, model_validator

from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.model_base import DomainModel, new_id, now_ms
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.task_commands import TaskCommand


class TaskExecutionTraceItem(DomainModel):
    step: str
    status: Literal["running", "success", "failed", "cancelled"] = "running"
    started_at: int = Field(default_factory=now_ms)
    duration_ms: int = 0
    error: str | None = None


class ArtifactReference(DomainModel):
    scope: Literal["project", "external"]
    path: str

    @model_validator(mode="after")
    def validate_path_scope(self) -> ArtifactReference:
        path = Path(self.path)
        if self.scope == "project":
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Project artifact paths must be project-relative")
            normalized = path.as_posix().strip()
            if not normalized or normalized == ".":
                raise ValueError("Project artifact paths cannot be empty")
            object.__setattr__(self, "path", normalized)
        elif not path.is_absolute():
            raise ValueError("External artifact paths must be absolute")
        else:
            object.__setattr__(self, "path", str(path.resolve()))
        return self

    @classmethod
    def project(cls, project_dir: str | Path, value: str | Path) -> ArtifactReference:
        root = Path(project_dir).resolve()
        path = Path(value)
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        return cls(scope="project", path=resolved.relative_to(root).as_posix())

    @classmethod
    def external(cls, value: str | Path) -> ArtifactReference:
        return cls(scope="external", path=str(Path(value).resolve()))

    @classmethod
    def from_path(
        cls,
        project_dir: str | Path,
        value: str | Path,
    ) -> ArtifactReference:
        root = Path(project_dir).resolve()
        path = Path(value)
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return cls.external(resolved)
        return cls.project(root, resolved)

    def resolve(self, project_dir: str | Path) -> Path:
        if self.scope == "external":
            return Path(self.path)
        return Path(project_dir).resolve() / self.path


class ImportedAssetTaskOutcome(DomainModel):
    outcome_type: Literal["imported_asset"] = "imported_asset"
    asset_id: str
    document_id: str | None = None
    purpose: Literal["media", "subtitle", "watermark"]


class DownloadAnalysisTaskOutcome(DomainModel):
    outcome_type: Literal["download_analysis"] = "download_analysis"
    plan: DownloadPlan


class SequenceBoundaryTaskOutcome(DomainModel):
    outcome_type: Literal["sequence_boundary"] = "sequence_boundary"
    analysis: SequenceBoundaryAnalysis


class LoudnessTaskOutcome(DomainModel):
    outcome_type: Literal["loudness"] = "loudness"
    sample_peak_dbfs: float
    true_peak_dbtp: float
    short_term_lufs: float
    integrated_lufs: float

    def desktop_payload(self) -> dict[str, float]:
        return {
            "samplePeakDbfs": self.sample_peak_dbfs,
            "truePeakDbtp": self.true_peak_dbtp,
            "shortTermLufs": self.short_term_lufs,
            "integratedLufs": self.integrated_lufs,
        }


class ExportFileTaskOutcome(DomainModel):
    output: ArtifactReference
    requested_video_codec: str | None = None
    actual_video_codec: str | None = None
    hardware_fallback_reason: str | None = None
    archived_failed_outputs: list[ArtifactReference] = Field(default_factory=list)

    @property
    def hardware_fallback_used(self) -> bool:
        return self.hardware_fallback_reason is not None


class ExportTaskOutcome(DomainModel):
    outcome_type: Literal["export"] = "export"
    files: list[ExportFileTaskOutcome]

    @property
    def hardware_fallback_used(self) -> bool:
        return any(item.hardware_fallback_used for item in self.files)


TaskOutcome = Annotated[
    ImportedAssetTaskOutcome
    | DownloadAnalysisTaskOutcome
    | SequenceBoundaryTaskOutcome
    | LoudnessTaskOutcome
    | ExportTaskOutcome,
    Field(discriminator="outcome_type"),
]

TaskStopRequest = Literal["pause", "cancel"]


class Task(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    sequence_id: str | None = None
    idempotency_key: str | None = None
    command: TaskCommand
    status: TaskStatus = TaskStatus.PENDING
    progress: OperationProgress = Field(default_factory=lambda: OperationProgress.indeterminate("queued"))
    input_asset_ids: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    outcome: TaskOutcome | None = None
    execution_trace: list[TaskExecutionTraceItem] = Field(default_factory=list)
    error: str | None = None
    execution_owner_id: str | None = None
    heartbeat_at: int | None = None
    lease_expires_at: int | None = None
    stop_request: TaskStopRequest | None = None
    revision: int = 0
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)

    @model_validator(mode="after")
    def coherent_execution_lease(self) -> Task:
        owner_id = self.execution_owner_id
        heartbeat_at = self.heartbeat_at
        lease_expires_at = self.lease_expires_at
        if self.status == TaskStatus.RUNNING:
            if (
                owner_id is None
                or heartbeat_at is None
                or lease_expires_at is None
            ):
                raise ValueError("Running tasks require an execution owner and lease")
            if lease_expires_at <= heartbeat_at:
                raise ValueError("Task lease expiry must be after its heartbeat")
        elif any(
            value is not None
            for value in (owner_id, heartbeat_at, lease_expires_at)
        ):
            raise ValueError("Only running tasks can hold an execution lease")
        if self.stop_request is not None and self.status != TaskStatus.RUNNING:
            raise ValueError("Only running tasks can carry a stop request")
        return self

    @computed_field
    @property
    def kind(self) -> TaskKind:
        return self.command.task_kind
