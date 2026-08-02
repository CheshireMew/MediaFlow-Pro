from __future__ import annotations

import builtins
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from mediaflow.application.events import TaskEvent
from mediaflow.domain.asr import AsrResult, RegionAsrPipeline
from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.downloads import DownloadPlan, DownloadRequest
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.frame_clock import MainFrameClockSnapshot
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset, AssetFingerprint, MediaMetadata, Project, ProjectProfile, Sequence
from mediaflow.domain.project_records import (
    ExportHistoryRecord,
    ExportQualityReport,
    ProjectVersionRecord,
)
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.settings import AsrSettings, DownloadSettings, LlmProviderSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitlePlacement, SubtitleSegment, SubtitleWord
from mediaflow.domain.tasks import LoudnessTaskOutcome, Task, TaskStopRequest
from mediaflow.domain.timeline import (
    Clip,
    ClipTransformKeyframe,
    TimelineMarker,
    TimelineRange,
    TimelineState,
)
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebAssetSpec,
    WebClipExportResult,
    WebClipState,
    WebExportFormat,
)
from mediaflow.domain.workflows import WorkflowRun


class MediaProbeResult(Protocol):
    @property
    def kind(self) -> AssetKind: ...

    @property
    def metadata(self) -> MediaMetadata: ...

    @property
    def suggested_profile(self) -> ProjectProfile | None: ...


class MediaProbePort(Protocol):
    def probe(
        self,
        path: str | Path,
        *,
        timeline_profile: ProjectProfile | None = None,
    ) -> MediaProbeResult: ...


FingerprintFile = Callable[[Path], AssetFingerprint]


class JsonClient(Protocol):
    def complete_json(self, *, system: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class JsonClientFactory(Protocol):
    def __call__(self, provider: LlmProviderSettings) -> JsonClient: ...


class TranslationCachePort(Protocol):
    def get(self, request: dict[str, Any]) -> list[str] | None: ...
    def put(self, request: dict[str, Any], texts: list[str]) -> None: ...


class TaskStore(Protocol):
    project_dir: Path
    read_only: bool

    def create(self, task: Task, *, event_type: str = "created") -> Task: ...
    def claim(
        self,
        task_id: str,
        owner_id: str,
        lease_duration_ms: int,
    ) -> tuple[Task, bool] | None: ...
    def renew_lease(
        self,
        task_id: str,
        owner_id: str,
        lease_duration_ms: int,
    ) -> Task | None: ...
    def update_owned(
        self,
        task: Task,
        owner_id: str,
        *,
        event_type: str,
        release_owner: bool = False,
    ) -> Task | None: ...
    def queue_paused(self, task_id: str) -> Task | None: ...
    def request_stop(
        self,
        task_id: str,
        request: TaskStopRequest,
    ) -> Task | None: ...
    def get(self, task_id: str) -> Task: ...
    def list(self) -> builtins.list[Task]: ...
    def list_unconsumed_terminal(self) -> builtins.list[Task]: ...
    def list_claimable(self, at_ms: int) -> builtins.list[Task]: ...
    def delete(self, task_id: str, *, event_type: str = "deleted") -> None: ...
    def delete_terminal(self) -> builtins.list[Task]: ...
    def snapshot(self) -> tuple[builtins.list[Task], int]: ...
    def latest_event(self, task_id: str) -> TaskEvent: ...
    def events_after(self, cursor: int, *, limit: int = 500) -> builtins.list[TaskEvent]: ...


class TaskProjectAccess(Protocol):
    project_dir: Path
    read_only: bool


class ProjectAccess(Protocol):
    """Project storage context and unit-of-work boundary."""

    project_dir: Path
    read_only: bool

    def transaction(self) -> AbstractContextManager[Any]: ...
    def enlist_transaction_publication(
        self,
        *,
        on_commit: Callable[[], None],
        on_rollback: Callable[[BaseException], None],
    ) -> None: ...
    def content_revision(self) -> int: ...


class ProjectRecordsDocuments(Protocol):
    def save_export_history(self, record: ExportHistoryRecord) -> ExportHistoryRecord: ...
    def list_export_history(self, sequence_id: str | None = None) -> list[ExportHistoryRecord]: ...
    def create_project_version(self, name: str) -> ProjectVersionRecord: ...
    def list_project_versions(self) -> list[ProjectVersionRecord]: ...
    def restore_project_version(self, version_id: str) -> ProjectVersionRecord: ...


class WorkflowDocuments(Protocol):
    def set_workflow_auto_continue(self, value: bool | None) -> Project: ...
    def save_workflow_run(self, run: WorkflowRun) -> WorkflowRun: ...
    def get_workflow_run(self, run_id: str) -> WorkflowRun: ...
    def list_workflow_runs(self, *, active_only: bool = False) -> list[WorkflowRun]: ...


class SequenceDocuments(Protocol):
    def list_sequences(self, *, include_archived: bool = False) -> list[Sequence]: ...
    def get_sequence(self, sequence_id: str) -> Sequence: ...
    def save_sequence_export_preset(
        self,
        sequence_id: str,
        preset: ExportPreset,
    ) -> Sequence: ...
    def create_short_sequence(
        self,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> Sequence: ...
    def archive_short_sequence(self, sequence_id: str) -> Sequence: ...
    def restore_short_sequence(self, sequence_id: str) -> Sequence: ...


class AssetDocuments(Protocol):
    def add_asset(self, asset: Asset) -> Asset: ...
    def prepare_external_asset(
        self,
        path: str | Path,
        kind: AssetKind,
    ) -> Asset: ...
    def commit_external_asset(self, asset: Asset) -> Asset: ...
    def import_external_asset(self, path: str | Path, kind: AssetKind) -> Asset: ...
    def get_asset(self, asset_id: str) -> Asset: ...
    def list_assets(self) -> list[Asset]: ...
    def update_asset(self, asset: Asset) -> Asset: ...
    def set_asset_proxy_paths(
        self,
        asset_id: str,
        *,
        expected_fingerprint: AssetFingerprint | None,
        proxy_path: str | Path,
        sdr_preview_proxy_path: str | Path | None,
    ) -> Asset: ...
    def set_asset_waveform_path(
        self,
        asset_id: str,
        *,
        expected_fingerprint: AssetFingerprint | None,
        waveform_path: str | Path,
    ) -> Asset: ...
    def refresh_asset_status(self, asset_id: str) -> Asset: ...
    def relink_asset(
        self,
        asset_id: str,
        replacement: str | Path,
        *,
        allow_different_content: bool = False,
    ) -> Asset: ...
    def resolve_asset_path(self, asset: Asset) -> Path: ...


class TimelineDocuments(Protocol):
    def load_timeline(self, sequence_id: str) -> TimelineState: ...
    def list_timeline_markers(self, sequence_id: str) -> list[TimelineMarker]: ...
    def list_timeline_ranges(self, sequence_id: str) -> list[TimelineRange]: ...
    def save_timeline(self, state: TimelineState) -> int: ...
    def save_clip_changes(self, state: TimelineState, clip_ids: set[str]) -> int: ...
    def capture_main_frame_clock(self, sequence_id: str) -> MainFrameClockSnapshot: ...
    def change_main_frame_clock(
        self,
        source: MainFrameClockSnapshot,
        state: TimelineState,
        assets: list[Asset],
        *,
        old_profile: ProjectProfile,
    ) -> MainFrameClockSnapshot: ...
    def restore_main_frame_clock(
        self,
        source: MainFrameClockSnapshot,
        destination: MainFrameClockSnapshot,
    ) -> MainFrameClockSnapshot: ...


class WebMediaDocuments(Protocol):
    def save_web_asset_spec(self, spec: WebAssetSpec) -> WebAssetSpec: ...
    def get_web_asset_spec(self, asset_id: str) -> WebAssetSpec: ...
    def list_web_asset_specs(self) -> list[WebAssetSpec]: ...
    def get_web_clip_state(self, clip_id: str) -> WebClipState: ...
    def list_web_clip_states(self, sequence_id: str) -> dict[str, WebClipState]: ...
    def save_web_clip_states(self, states: list[WebClipState]) -> None: ...


class WebPackageValidatorPort(Protocol):
    def validate(self, package_root: Path, manifest: EditableMediaManifest) -> None: ...


class AudioDocuments(Protocol):
    def list_audio_buses(self, sequence_id: str) -> list[AudioBus]: ...
    def save_audio_bus(self, bus: AudioBus) -> AudioBus: ...
    def replace_audio_graph(
        self,
        sequence_id: str,
        buses: list[AudioBus],
        effects: list[AudioEffect],
    ) -> None: ...
    def save_audio_effect(self, effect: AudioEffect) -> AudioEffect: ...
    def list_audio_effects(self, bus_id: str) -> list[AudioEffect]: ...
    def save_audio_effect_chain(
        self,
        bus_id: str,
        effects: list[AudioEffect],
    ) -> list[AudioEffect]: ...
    def remove_audio_effect(self, effect_id: str) -> None: ...


class SubtitleDocuments(Protocol):
    def get_asset_transcript(
        self,
        asset_id: str,
        signature: str,
    ) -> AsrResult | None: ...
    def save_asset_transcript(
        self,
        asset_id: str,
        signature: str,
        result: AsrResult,
    ) -> AsrResult: ...
    def create_subtitle_document(
        self,
        document: SubtitleDocument,
        segments: list[SubtitleSegment],
        words: list[SubtitleWord] | None = None,
    ) -> SubtitleDocument: ...
    def save_subtitle_document(self, document: SubtitleDocument) -> SubtitleDocument: ...
    def get_subtitle_document(self, document_id: str) -> SubtitleDocument: ...
    def list_subtitle_documents(
        self,
        asset_id: str | None = None,
        *,
        sequence_id: str | None = None,
    ) -> list[SubtitleDocument]: ...
    def list_subtitle_segments(self, document_id: str) -> list[SubtitleSegment]: ...
    def subtitle_segment_summary(self, document_id: str) -> tuple[int, int, int]: ...
    def save_subtitle_segments(
        self,
        document_id: str,
        segments: list[SubtitleSegment],
    ) -> None: ...
    def list_subtitle_words(
        self,
        document_id: str,
        *,
        include_excluded: bool = True,
    ) -> list[SubtitleWord]: ...
    def save_subtitle_words(
        self,
        document_id: str,
        words: list[SubtitleWord],
    ) -> None: ...
    def place_subtitle_document(
        self,
        document_id: str,
        track_id: str,
        *,
        offset_frames: int = 0,
        source_start_frame: int | None = None,
        source_end_frame: int | None = None,
        follow_clips: bool | None = None,
    ) -> list[SubtitlePlacement]: ...
    def list_subtitle_placements(self, track_id: str) -> list[SubtitlePlacement]: ...
    def get_subtitle_placement(self, placement_id: str) -> SubtitlePlacement: ...
    def update_subtitle_placement_text(
        self,
        placement_id: str,
        text_override: str | None,
    ) -> SubtitlePlacement: ...
    def update_subtitle_placement_range(
        self,
        placement_id: str,
        start_frame: int,
        end_frame: int,
        *,
        timing_overridden: bool = True,
    ) -> SubtitlePlacement: ...
    def reset_subtitle_placement_range(self, placement_id: str) -> SubtitlePlacement: ...
    def add_subtitle_placements(
        self,
        placements: list[SubtitlePlacement],
    ) -> list[SubtitlePlacement]: ...
    def apply_subtitle_placement_to_document(
        self,
        placement_id: str,
        text: str,
    ) -> SubtitleSegment: ...


class HighlightDocuments(Protocol):
    def save_highlights(self, candidates: list[HighlightCandidate]) -> None: ...
    def list_highlights(
        self,
        asset_id: str | None = None,
    ) -> list[HighlightCandidate]: ...
    def delete_highlight(self, candidate_id: str) -> None: ...


class ProjectCatalogDocuments(
    WorkflowDocuments,
    SequenceDocuments,
    AssetDocuments,
    Protocol,
):
    """Project metadata, sequences, assets and workflow records."""

    def get_project(self) -> Project: ...


class AssetServiceDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...


class TimelineValidationDocuments(Protocol):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...


class WebApplicationDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class HighlightServiceDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def highlights(self) -> HighlightDocuments: ...


class SequenceServiceDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class SubtitleAcquisitionDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...


class SubtitleEditingDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...


class TimelineEditorDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class TranscriptEditingDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def records(self) -> ProjectRecordsDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class SubtitlePublicationDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...


class TranslationDocuments(ProjectAccess, Protocol):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...


class WorkflowCoordinatorDocuments(ProjectAccess, Protocol):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...


class ProjectWorkflowDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def highlights(self) -> HighlightDocuments: ...


class AssetProcessingDocuments(ProjectAccess, Protocol):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...


class TimelineCompilationDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class InterchangeExportDocuments(ProjectAccess, Protocol):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class WebTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def timeline(self) -> TimelineDocuments: ...


class AssetTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...


class ExportTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def highlights(self) -> HighlightDocuments: ...

    @property
    def records(self) -> ProjectRecordsDocuments: ...


class TranscriptionTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class AnalysisTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def catalog(self) -> ProjectCatalogDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class ProjectTaskDocuments(
    AssetTaskDocuments,
    WebTaskDocuments,
    ExportTaskDocuments,
    TranscriptionTaskDocuments,
    AnalysisTaskDocuments,
    Protocol,
):
    """Complete persistence surface used only by the task composition root."""


ProgressCallback = Callable[[OperationProgress], None]
CancellationCheck = Callable[[], None]


class WebTaskRuntime(Protocol):
    def render_web_export(
        self,
        state: TimelineState,
        clip_id: str,
        output_path: str | Path,
        format: WebExportFormat,
        *,
        time_ms: int,
        background: str,
        overwrite: bool,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> WebClipExportResult: ...

    def render_web_clip(
        self,
        state: TimelineState,
        clip_id: str,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> Path: ...


class AssetTaskRuntime(Protocol):
    def generate_proxy(
        self,
        asset: Asset,
        profile: ProjectProfile,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> Asset: ...

    def generate_waveform(
        self,
        asset: Asset,
        *,
        duration_seconds: float,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> Asset: ...


class DownloadTaskRuntime(Protocol):
    def download_media(
        self,
        request: DownloadRequest,
        settings: DownloadSettings,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> list[Path]: ...

    def archive_unrecorded_downloads(
        self,
        paths: list[Path],
    ) -> tuple[Path, ...]: ...


class ExportExecutionResult(Protocol):
    @property
    def output_path(self) -> Path: ...

    @property
    def project_graph_path(self) -> Path: ...

    @property
    def subtitle_files(self) -> tuple[Path, ...]: ...

    @property
    def requested_video_codec(self) -> str | None: ...

    @property
    def actual_video_codec(self) -> str | None: ...

    @property
    def hardware_fallback_reason(self) -> str | None: ...

    @property
    def hardware_failure_details(self) -> str | None: ...

    @property
    def archived_failed_outputs(self) -> tuple[Path, ...]: ...


@dataclass(frozen=True, slots=True)
class ExportSequenceRequest:
    state: TimelineState
    preset: ExportPreset
    output_path: str | Path


class ExportTaskRuntime(Protocol):
    def preflight_sequence_exports(
        self,
        requests: list[ExportSequenceRequest],
        *,
        overwrite: bool,
    ) -> None: ...

    def export_sequence(
        self,
        state: TimelineState,
        preset: ExportPreset,
        output_path: str | Path,
        *,
        overwrite: bool,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> ExportExecutionResult: ...

    def export_sequences_atomically(
        self,
        requests: list[ExportSequenceRequest],
        *,
        overwrite: bool,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> tuple[ExportExecutionResult, ...]: ...

    def archive_unrecorded_exports(
        self,
        results: list[ExportExecutionResult],
        *,
        quality_report_id: str | None = None,
    ) -> tuple[Path, ...]: ...

    def analyze_export_quality(
        self,
        state: TimelineState,
        preset: ExportPreset,
        result: ExportExecutionResult,
        *,
        report_id: str,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> tuple[ExportQualityReport, Path]: ...


class TranscriptionTaskRuntime(Protocol):
    def create_asr_pipeline(
        self,
        settings: AsrSettings,
        *,
        check_cancelled: CancellationCheck,
    ) -> RegionAsrPipeline: ...

    def fingerprint_matches(
        self,
        path: Path,
        fingerprint: AssetFingerprint,
    ) -> bool: ...

    def fingerprint_file(self, path: Path) -> AssetFingerprint: ...


class AnalysisTaskRuntime(Protocol):
    def analyze_sequence_bounds(
        self,
        state: TimelineState,
        *,
        expected_snapshot_hash: str,
        check_cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> tuple[SequenceBoundaryAnalysis, Path]: ...

    def analyze_loudness(
        self,
        state: TimelineState,
        *,
        check_cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> tuple[LoudnessTaskOutcome, Path]: ...

    def detect_scenes(
        self,
        source: Path,
        clip: Clip,
        profile: ProjectProfile,
        *,
        threshold: float,
        check_cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> list[int]: ...

    def track_subject(
        self,
        source: Path,
        clip: Clip,
        profile: ProjectProfile,
        *,
        mode: Literal["auto_reframe", "subject_tracking"],
        check_cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> list[ClipTransformKeyframe]: ...

    def write_visual_analysis(
        self,
        path: Path,
        payload: dict[str, Any],
    ) -> Path: ...

    def analyze_download(
        self,
        url: str,
        settings: DownloadSettings,
        *,
        check_cancelled: CancellationCheck,
    ) -> DownloadPlan: ...


class ProjectTaskRuntimePorts(Protocol):
    """Focused technical ports assembled for one project task registry."""

    @property
    def web(self) -> WebTaskRuntime: ...

    @property
    def assets(self) -> AssetTaskRuntime: ...

    @property
    def downloads(self) -> DownloadTaskRuntime: ...

    @property
    def exports(self) -> ExportTaskRuntime: ...

    @property
    def transcription(self) -> TranscriptionTaskRuntime: ...

    @property
    def analysis(self) -> AnalysisTaskRuntime: ...
