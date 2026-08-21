from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, model_serializer, model_validator

from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.collaboration import ProjectChangeEvent
from mediaflow.domain.dubbing import DubbingSession, DubbingSettings
from mediaflow.domain.enums import (
    ColorMode,
    ExportFormat,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.exports import ExportPreset, SubtitleStyle
from mediaflow.domain.media_resources import (
    MediaResourceCatalogItem,
    MediaResourceCategory,
)
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.portable_timeline import PortableTimelineProfile
from mediaflow.domain.project import Asset, Project, ProjectProfile, Sequence
from mediaflow.domain.project_records import ExportHistoryRecord, ProjectVersionRecord
from mediaflow.domain.reference_comparison import (
    ReferenceComparisonAcceptance,
    ReferenceComparisonResult,
)
from mediaflow.domain.runtime_capabilities import RuntimeInspection
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.domain.task_commands import SequenceBuildUnit, TaskCommand
from mediaflow.domain.tasks import Task
from mediaflow.domain.timeline import (
    Clip,
    ClipAddRequest,
    ClipAudio,
    ClipTransform,
    FreezeClipAddRequest,
    TimelineMarker,
    TimelineState,
    Track,
    Transition,
)
from mediaflow.domain.transcript_edits import (
    TranscriptEditPlan,
    TranscriptEditRequest,
    TranscriptEditResult,
    TranscriptSnapshot,
)
from mediaflow.domain.web_exports import WebExportFormat
from mediaflow.domain.web_manifest import (
    WebAssetSpec,
    web_asset_spec_document,
)
from mediaflow.domain.web_manifest_primitives import WebEditableField
from mediaflow.domain.web_state import (
    WebClipState,
    WebEasing,
    WebEditDocument,
    WebRebindCommitReport,
    WebRebindPlan,
    WebStateDiff,
    WebVariantResult,
)
from mediaflow.domain.workflows import WorkflowRun

Actor = Literal["human", "automation"]


class PublicWebAssetSpec(WebAssetSpec):
    @model_serializer(mode="plain")
    def serialize_public_document(self) -> dict[str, JsonValue]:
        return web_asset_spec_document(self)


class EmptyArguments(DomainModel):
    pass


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


class SequenceArguments(DomainModel):
    sequence_id: str | None = None


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


class TranscriptSequenceTranscribeArguments(SequenceArguments):
    asr: AsrSettings | None = None
    start_frame: int | None = Field(default=None, ge=0)
    end_frame: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_range(self) -> TranscriptSequenceTranscribeArguments:
        if self.start_frame is not None and self.end_frame is not None and self.end_frame <= self.start_frame:
            raise ValueError("end_frame must be after start_frame")
        return self


class PortableTimelineArguments(SequenceArguments):
    timeline_path: str = Field(min_length=1)


class TimelineTrackAddArguments(SequenceArguments):
    kind: TrackKind
    name: str | None = None


class TimelineClipAddArguments(ClipAddRequest):
    sequence_id: str | None = None


class TimelineClipBatchAddArguments(SequenceArguments):
    clips: list[ClipAddRequest] = Field(min_length=1, max_length=1000)


class TimelineFreezeClipAddArguments(FreezeClipAddRequest):
    sequence_id: str | None = None


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


class TimelineTransitionAddArguments(SequenceArguments):
    left_clip_id: str = Field(min_length=1)
    right_clip_id: str = Field(min_length=1)
    kind: TransitionKind
    duration: int = Field(gt=0)


class TimelineTransitionUpdateArguments(SequenceArguments):
    transition_id: str = Field(min_length=1)
    kind: TransitionKind
    duration: int = Field(gt=0)
    parameters: dict[str, JsonValue] | None = None


class TimelineTransitionRemoveArguments(SequenceArguments):
    transition_id: str = Field(min_length=1)


class TimelineMarkerAddArguments(SequenceArguments):
    frame: int = Field(ge=0)
    name: str = ""
    color: str = Field(default="#4ea1ff", pattern="^#[0-9a-fA-F]{6}$")


class TimelineMarkerUpdateArguments(SequenceArguments):
    marker_id: str = Field(min_length=1)
    frame: int = Field(ge=0)
    name: str = ""
    color: str = Field(pattern="^#[0-9a-fA-F]{6}$")


class TimelineMarkerRemoveArguments(SequenceArguments):
    marker_id: str = Field(min_length=1)


class SubtitleTrackStyleUpdateArguments(SequenceArguments):
    track_id: str = Field(min_length=1)
    style: SubtitleStyle


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
    resource_asset_id: str | None = Field(default=None, min_length=1)


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


class ScriptSegmentUpdateArguments(DomainModel):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    text: str | None = None
    speaker: str | None = None

    @model_validator(mode="after")
    def has_change(self) -> ScriptSegmentUpdateArguments:
        if not ({"text", "speaker"} & self.model_fields_set):
            raise ValueError("Script segment update must change text or speaker")
        if "text" in self.model_fields_set:
            if self.text is None or not self.text.strip():
                raise ValueError("Script segment text cannot be empty")
            object.__setattr__(self, "text", " ".join(self.text.split()))
        return self


class ScriptSegmentSplitArguments(DomainModel):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    split_frame: int | None = Field(default=None, ge=0)
    split_index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def one_split_point(self) -> ScriptSegmentSplitArguments:
        if (self.split_frame is None) == (self.split_index is None):
            raise ValueError("Specify exactly one of split_frame or split_index")
        return self


class ScriptSegmentMergeArguments(DomainModel):
    document_id: str = Field(min_length=1)
    segment_ids: list[str] = Field(min_length=2)

    @model_validator(mode="after")
    def unique_segments(self) -> ScriptSegmentMergeArguments:
        object.__setattr__(
            self,
            "segment_ids",
            list(dict.fromkeys(value.strip() for value in self.segment_ids if value.strip())),
        )
        if len(self.segment_ids) < 2:
            raise ValueError("At least two distinct script segments are required")
        return self


class ScriptSegmentMoveArguments(SequenceArguments):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    position: int = Field(ge=0)
    expected_content_revision: int = Field(ge=0)


class ScriptGapCloseArguments(SequenceArguments):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    expected_content_revision: int = Field(ge=0)


class TranscriptEditPreviewArguments(DomainModel):
    edit: TranscriptEditRequest


class TranscriptEditApplyArguments(DomainModel):
    plan: TranscriptEditPlan
    accept_warnings: bool | None = None


class DubbingPrepareArguments(SequenceArguments):
    source_document_id: str = Field(min_length=1)
    target_language: str = Field(default="zh_CN", min_length=1)
    target_document_id: str | None = None
    settings: DubbingSettings = Field(default_factory=DubbingSettings)


class DubbingSessionArguments(DomainModel):
    session_id: str = Field(min_length=1)


class DubbingListArguments(SequenceArguments):
    pass


class DubbingSynthesizeArguments(SequenceArguments):
    session_id: str = Field(min_length=1)
    utterance_ids: list[str] = Field(default_factory=list)
    regenerate: bool = False


class DubbingCommitArguments(SequenceArguments):
    session_id: str = Field(min_length=1)
    track_name: str = Field(default="中文配音", min_length=1)
    mute_source_dialogue: bool = True


class DubbingSpeakerUpdateArguments(DomainModel):
    session_id: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    display_name: str = Field(min_length=1)
    review_status: Literal["automatic", "accepted", "needs_review"]
    primary_reference_id: str = Field(min_length=1)


class DubbingReferenceUpdateArguments(DomainModel):
    session_id: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    text: str = Field(min_length=1)
    language: str = Field(min_length=1)


class DubbingUtteranceUpdateArguments(DomainModel):
    session_id: str = Field(min_length=1)
    utterance_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    target_text: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    review_status: Literal["automatic", "accepted", "needs_review"]


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


class PreviewFramesRenderArguments(PreviewRenderArguments):
    frames: list[int] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def valid_frames(self) -> PreviewFramesRenderArguments:
        if any(type(frame) is not int or frame < 0 for frame in self.frames):
            raise ValueError("frames must contain non-negative integers")
        if len(set(self.frames)) != len(self.frames):
            raise ValueError("frames must not contain duplicates")
        return self


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


class TaskWaitArguments(TaskStatusArguments):
    timeout: float = Field(default=3600, gt=0, le=86_400)


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


class TaskReceiptResult(DomainModel):
    task: Task


class SequenceResult(DomainModel):
    sequence: Sequence


class TimelineResult(DomainModel):
    timeline: TimelineState


class PortableTimelineInspectResult(DomainModel):
    timeline_path: str
    timeline_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    project_id: str
    profile: PortableTimelineProfile
    duration_seconds: float = Field(gt=0)
    source_count: int = Field(ge=0)
    track_count: int = Field(gt=0)
    clip_count: int = Field(ge=0)
    marker_count: int = Field(ge=0)
    mediaflow_compatible: Literal[True] = True


class PortableTimelineImportResult(PortableTimelineInspectResult):
    timeline: TimelineState
    source_assets: dict[str, Asset]
    subtitle_document_ids: list[str]


class TrackResult(DomainModel):
    track: Track


class ClipResult(DomainModel):
    clip: Clip


class ClipsResult(DomainModel):
    clips: list[Clip]


class TransitionResult(DomainModel):
    transition: Transition


class MarkerResult(DomainModel):
    marker: TimelineMarker


class SubtitleDocumentWithSegments(SubtitleDocument):
    segments: list[SubtitleSegment]


class SubtitleListResult(DomainModel):
    documents: list[SubtitleDocumentWithSegments]


class SubtitleSegmentResult(DomainModel):
    segment: SubtitleSegment


class DubbingSessionResult(DomainModel):
    session: DubbingSession


class DubbingSessionListResult(DomainModel):
    sessions: list[DubbingSession]


class TranscriptResult(DomainModel):
    transcript: TranscriptSnapshot


class ScriptParagraph(DomainModel):
    position: int = Field(ge=0)
    segment: SubtitleSegment
    words: list[SubtitleWord]
    timeline_start_frame: int = Field(ge=0)
    timeline_end_frame: int = Field(gt=0)
    gap_before_frames: int = Field(ge=0)
    overlap_with_previous_frames: int = Field(ge=0)
    timing_precision: Literal[
        "recognized_words",
        "mixed_words",
        "estimated_words",
        "segment_only",
    ]


class ScriptInspectResult(DomainModel):
    content_revision: int = Field(ge=0)
    sequence_id: str
    timeline_duration_frames: int = Field(ge=0)
    document: SubtitleDocument
    paragraphs: list[ScriptParagraph]
    recognized_word_count: int = Field(ge=0)
    estimated_word_count: int = Field(ge=0)
    deletion_workflow: Literal["transcript.edit.preview -> transcript.edit.apply"] = (
        "transcript.edit.preview -> transcript.edit.apply"
    )


class ScriptSegmentSplitResult(DomainModel):
    segments: list[SubtitleSegment] = Field(min_length=2, max_length=2)


class ScriptTimelineEditResult(DomainModel):
    segment: SubtitleSegment
    recovery_version: ProjectVersionRecord
    content_revision: int = Field(ge=0)
    before_duration_frames: int = Field(ge=0)
    after_duration_frames: int = Field(ge=0)
    changed_timeline_frames: int = Field(ge=1)


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


class PreviewProofFrame(DomainModel):
    frame: int = Field(ge=0)
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    byte_count: int = Field(gt=0)


class PreviewFramesRenderResult(DomainModel):
    content_revision: int = Field(ge=0)
    preview_graph: str
    frames: list[PreviewProofFrame]


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
