from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_serializer, model_validator

from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.enums import ExportFormat, TrackKind, VisualEffectKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.project import Asset, Project, ProjectProfile, Sequence
from mediaflow.domain.project_records import ProjectVersionRecord
from mediaflow.domain.reference_comparison import (
    ReferenceComparisonAcceptance,
    ReferenceComparisonResult,
)
from mediaflow.domain.runtime_capabilities import RuntimeInspection
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import SequenceBuildUnit, TaskCommand
from mediaflow.domain.tasks import Task
from mediaflow.domain.timeline import (
    Clip,
    ClipAddRequest,
    ClipAudio,
    ClipTransform,
    TimelineState,
    Track,
)
from mediaflow.domain.transcript_edits import (
    TranscriptEditPlan,
    TranscriptEditRequest,
    TranscriptEditResult,
    TranscriptSnapshot,
)
from mediaflow.domain.web_media import (
    WebAssetSpec,
    WebClipState,
    WebEasing,
    WebEditableField,
    WebEditDocument,
    WebExportFormat,
    WebRebindCommitReport,
    WebRebindPlan,
    WebStateDiff,
    WebVariantResult,
    web_asset_spec_document,
)
from mediaflow.domain.workflows import WorkflowRun

Actor = Literal["human", "automation"]


class PublicWebAssetSpec(WebAssetSpec):
    @model_serializer(mode="plain")
    def serialize_public_document(self) -> dict[str, JsonValue]:
        return web_asset_spec_document(self)


class EmptyArguments(DomainModel):
    pass


class SpeechTranscribeArguments(DomainModel):
    input_path: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    language: str | None = None
    model: str | None = None
    device: Literal["auto", "cuda", "cpu"] | None = None
    compute_type: str | None = None
    overwrite: bool = False


class SpeechSegmentResult(DomainModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def positive_range(self) -> SpeechSegmentResult:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Speech segment end must be after start")
        return self


class SpeechTranscriptionResult(DomainModel):
    engine: Literal["faster-whisper-xxl"] = "faster-whisper-xxl"
    engine_version: str
    input_path: str
    input_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    output_path: str
    output_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    language: str
    duration_seconds: float = Field(gt=0)
    segments: list[SpeechSegmentResult] = Field(min_length=1)


class SpeechSynthesizeArguments(DomainModel):
    text: str = Field(min_length=1)
    text_language: str = Field(min_length=1)
    reference_audio: str = Field(min_length=1)
    reference_text: str = Field(min_length=1)
    reference_language: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    auxiliary_reference_audio: list[str] = Field(default_factory=list, max_length=5)
    speed_factor: float = Field(default=1.0, ge=0.5, le=2.0)
    seed: int = -1
    timeout_seconds: int = Field(default=900, ge=30, le=3600)
    overwrite: bool = False


class SpeechSynthesisResult(DomainModel):
    engine: Literal["gpt-sovits-v2pro"] = "gpt-sovits-v2pro"
    engine_version: str
    output_path: str
    output_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    duration_seconds: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    reference_audio_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    device: Literal["cuda", "cpu"]


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


class SequenceArguments(DomainModel):
    sequence_id: str | None = None


class TimelineTrackAddArguments(SequenceArguments):
    kind: TrackKind
    name: str | None = None


class TimelineClipAddArguments(ClipAddRequest):
    sequence_id: str | None = None


class TimelineClipBatchAddArguments(SequenceArguments):
    clips: list[ClipAddRequest] = Field(min_length=1, max_length=1000)


class TimelineClipMoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    timeline_start: int = Field(ge=0)
    track_id: str | None = None


class TimelineClipSplitArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    split_frame: int = Field(gt=0)


class TimelineClipDeleteArguments(SequenceArguments):
    clip_ids: list[str] = Field(min_length=1)
    ripple: bool | None = None


class TimelineClipTransformArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    transform: ClipTransform


class TimelineClipAudioArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    audio: ClipAudio


class TimelineClipReplaceSourceArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)


class TimelineClipVisualEffectAddArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    kind: VisualEffectKind


class TimelineClipVisualEffectUpdateArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    enabled: bool
    parameters: dict[str, float]


class TimelineClipVisualEffectMoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    position: int = Field(ge=0)


class TimelineClipVisualEffectRemoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)


class SubtitleSegmentUpdateArguments(DomainModel):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def positive_range(self) -> SubtitleSegmentUpdateArguments:
        if self.end_frame <= self.start_frame:
            raise ValueError("end_frame must be after start_frame")
        return self


class TranscriptGetArguments(SequenceArguments):
    document_id: str | None = None


class TranscriptEditPreviewArguments(DomainModel):
    edit: TranscriptEditRequest


class TranscriptEditApplyArguments(DomainModel):
    plan: TranscriptEditPlan
    accept_warnings: bool | None = None


class AudioBusChanges(DomainModel):
    name: str | None = None
    parent_bus_id: str | None = None
    position: int | None = None
    gain_db: float | None = None
    muted: bool | None = None
    solo: bool | None = None
    channel_layout: Literal["mono", "stereo", "5.1"] | None = None


class AudioBusUpdateArguments(DomainModel):
    bus_id: str = Field(min_length=1)
    changes: AudioBusChanges


class AudioEffectSaveArguments(DomainModel):
    effect: AudioEffect


class AudioEffectRemoveArguments(DomainModel):
    effect_id: str = Field(min_length=1)


class PreviewRenderArguments(SequenceArguments):
    use_proxies: bool | None = None


class ExportSequenceArguments(SequenceArguments):
    output_path: str = Field(min_length=1)
    format: ExportFormat | None = None
    preset: ExportPreset | None = None
    overwrite: bool | None = None
    timeout: float | None = Field(default=None, gt=0)


class BuildSequenceArguments(SequenceArguments):
    units: list[SequenceBuildUnit] = Field(min_length=1)
    output_path: str = Field(min_length=1)
    format: ExportFormat | None = None
    preset: ExportPreset | None = None
    overwrite: bool | None = None
    timeout: float | None = Field(default=None, gt=0)


class ExportFcpxmlArguments(SequenceArguments):
    output_path: str = Field(min_length=1)
    overwrite: bool | None = None


class TaskStatusArguments(DomainModel):
    task_id: str = Field(min_length=1)


class TaskStartArguments(SequenceArguments):
    task_command: TaskCommand
    input_asset_ids: list[str] | None = None
    timeout: float | None = Field(default=None, gt=0)


class TaskResumeArguments(DomainModel):
    task_id: str = Field(min_length=1)
    timeout: float | None = Field(default=None, gt=0)


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


class AssetListResult(DomainModel):
    assets: list[Asset]


class AutomationWorkflowResult(DomainModel):
    selected_asset_ids: list[str]
    status_message: str


class AutomationTaskApplicationResult(DomainModel):
    workflow: AutomationWorkflowResult
    imported_asset_id: str
    imported_document_id: str
    imported_purpose: str
    download_plan: DownloadPlan | None
    sequence_bounds_status: str
    sequence_id: str
    audio_metrics: dict[str, float] | None


class TaskCompletionResult(DomainModel):
    task: Task
    result: AutomationTaskApplicationResult


class AssetImportResult(TaskCompletionResult):
    asset: Asset


class SequenceResult(DomainModel):
    sequence: Sequence


class TimelineResult(DomainModel):
    timeline: TimelineState


class TrackResult(DomainModel):
    track: Track


class ClipResult(DomainModel):
    clip: Clip


class ClipsResult(DomainModel):
    clips: list[Clip]


class SubtitleDocumentWithSegments(SubtitleDocument):
    segments: list[SubtitleSegment]


class SubtitleListResult(DomainModel):
    documents: list[SubtitleDocumentWithSegments]


class SubtitleSegmentResult(DomainModel):
    segment: SubtitleSegment


class TranscriptResult(DomainModel):
    transcript: TranscriptSnapshot


class TranscriptEditPlanResult(DomainModel):
    plan: TranscriptEditPlan


class TranscriptEditResultDocument(DomainModel):
    edit: TranscriptEditResult


class AudioBusWithEffects(AudioBus):
    effects: list[AudioEffect]


class AudioInspectResult(DomainModel):
    buses: list[AudioBusWithEffects]


class AudioBusResult(DomainModel):
    bus: AudioBus


class AudioEffectResult(DomainModel):
    effect: AudioEffect


class RemovedResult(DomainModel):
    removed: Literal[True]


class PreviewRenderResult(DomainModel):
    preview_graph: str


class FcpxmlExportResult(DomainModel):
    format: Literal["fcpxml"] = "fcpxml"
    project_id: str
    sequence_id: str
    timeline_revision: int = Field(ge=0)
    output_path: str
    sha256: str = Field(pattern="^[a-f0-9]{64}$")


class TaskListResult(DomainModel):
    tasks: list[Task]


class TaskStatusResult(DomainModel):
    task: Task


class WebImportResult(DomainModel):
    asset: Asset
    web_asset: PublicWebAssetSpec


class WebInspectResult(DomainModel):
    web_asset: PublicWebAssetSpec


class WebClipStateResult(DomainModel):
    web_clip_state: WebClipState


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


RuntimeInspectionResult = RuntimeInspection
