from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from mediaflow.domain.asr import RegionAsrPipeline
from mediaflow.domain.downloads import DownloadPlan, DownloadRequest
from mediaflow.domain.dubbing import DiarizationResult, DiarizationSpeechInterval
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset, AssetFingerprint, ProjectProfile
from mediaflow.domain.project_records import ExportQualityReport
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.settings import (
    AsrSettings,
    DownloadSettings,
    ServiceSettings,
    SpeakerDiarizationSettings,
)
from mediaflow.domain.task_commands import DiagnosticsBundleCommand, SequenceBuildUnit
from mediaflow.domain.tasks import LoudnessTaskOutcome
from mediaflow.domain.timeline import Clip, ClipTransformKeyframe, TimelineState
from mediaflow.domain.web_exports import WebClipExportResult, WebExportFormat

ProgressCallback = Callable[[OperationProgress], None]
CancellationCheck = Callable[[], None]


class AnalysisOutputPublication(Protocol):
    @property
    def archived_outputs(self) -> tuple[Path, ...]: ...

    def temporary_path(
        self,
        destination: str | Path,
        label: str,
    ) -> Path: ...

    def publish(self) -> None: ...

    def finalize(
        self,
        *,
        archive_replaced_to: Path | None = None,
    ) -> None: ...


class AnalysisOutputTransaction(
    AbstractContextManager[AnalysisOutputPublication],
    Protocol,
):
    pass


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


class PreparedProxyTaskResult(Protocol):
    @property
    def proxy_path(self) -> Path: ...

    @property
    def sdr_preview_proxy_path(self) -> Path | None: ...


class AssetTaskRuntime(Protocol):
    def prepare_proxy(
        self,
        asset: Asset,
        profile: ProjectProfile,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> PreparedProxyTaskResult: ...

    def commit_proxy(
        self,
        prepared: PreparedProxyTaskResult,
    ) -> Asset: ...

    def generate_waveform(
        self,
        asset: Asset,
        *,
        duration_seconds: float,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> Path: ...


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


@dataclass(frozen=True, slots=True)
class SequenceBuildUnitResult:
    unit: SequenceBuildUnit
    status: Literal["rendered", "reused"]
    cache_key: str
    output_path: Path
    sha256: str
    requested_video_codec: str | None
    actual_video_codec: str | None
    hardware_fallback_reason: str | None
    hardware_failure_details: str | None
    archived_failed_outputs: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class SequenceBuildAudioResult:
    status: Literal["rendered", "reused", "absent"]
    cache_key: str | None
    output_path: Path | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class SequenceBuildResult:
    export: ExportExecutionResult
    units: tuple[SequenceBuildUnitResult, ...]
    audio: SequenceBuildAudioResult
    assembly_status: Literal["assembled", "reused"]
    assembly_key: str


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

    def build_sequence(
        self,
        state: TimelineState,
        preset: ExportPreset,
        units: list[SequenceBuildUnit],
        output_path: str | Path,
        *,
        overwrite: bool,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> SequenceBuildResult: ...

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
    def output_transaction(
        self,
        destinations: Iterable[str | Path],
        *,
        overwrite: bool,
    ) -> AnalysisOutputTransaction: ...

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

    def write_download_analysis(self, path: Path, plan: DownloadPlan) -> Path: ...

    def archive_failed_visual_analysis(
        self,
        sources: tuple[Path, ...],
        archive_root: Path,
        task_id: str,
    ) -> list[str]: ...

    def analyze_download(
        self,
        url: str,
        settings: DownloadSettings,
        *,
        check_cancelled: CancellationCheck,
    ) -> DownloadPlan: ...


class DiagnosticsTaskRuntime(Protocol):
    def create_bundle(
        self,
        command: DiagnosticsBundleCommand,
        *,
        check_cancelled: CancellationCheck,
        report: ProgressCallback,
    ) -> tuple[Path, str, int, int]: ...


class PreparedDubbingAudio(Protocol):
    @property
    def path(self) -> Path: ...
    @property
    def sha256(self) -> str: ...
    @property
    def duration_seconds(self) -> float: ...
    @property
    def sample_rate(self) -> int: ...
    @property
    def channels(self) -> int: ...


class DubbingSynthesisOutput(Protocol):
    @property
    def output_path(self) -> Path: ...
    @property
    def sha256(self) -> str: ...
    @property
    def duration_seconds(self) -> float: ...


class DubbingSynthesisSession(Protocol):
    def synthesize(
        self,
        *,
        text: str,
        text_language: str,
        reference_audio: str | Path,
        reference_text: str,
        reference_language: str,
        output_path: str | Path,
        auxiliary_reference_audio: list[str | Path] | None = None,
        speed_factor: float = 1.0,
        seed: int = -1,
        timeout_seconds: int = 900,
        overwrite: bool = False,
    ) -> DubbingSynthesisOutput: ...


class DubbingTaskRuntime(Protocol):
    def file_sha256(self, path: Path) -> str: ...

    def archive_unrecorded_outputs(
        self,
        paths: list[Path],
    ) -> tuple[Path, ...]: ...

    def render_dialogue_audio(
        self,
        state: TimelineState,
        dialogue_track_id: str,
        output_path: str | Path,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio: ...

    def diarize(
        self,
        source: str | Path,
        settings: SpeakerDiarizationSettings,
        *,
        minimum_speakers: int | None,
        maximum_speakers: int | None,
        speech_intervals: tuple[DiarizationSpeechInterval, ...],
        check_cancelled: CancellationCheck,
    ) -> DiarizationResult: ...

    def extract_reference(
        self,
        source: str | Path,
        output_path: str | Path,
        *,
        start_seconds: float,
        end_seconds: float,
        sample_rate: int,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio: ...

    def synthesis_session(
        self,
        settings: ServiceSettings,
        *,
        check_cancelled: CancellationCheck,
    ) -> tuple[str, AbstractContextManager[DubbingSynthesisSession]]: ...

    def normalize_utterance(
        self,
        source: str | Path,
        output_path: str | Path,
        *,
        target_seconds: float | None,
        sample_rate: int,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio: ...

    def assemble_master(
        self,
        inputs: list[tuple[str | Path, float]],
        output_path: str | Path,
        *,
        minimum_duration_seconds: float,
        sample_rate: int,
        check_cancelled: CancellationCheck,
    ) -> PreparedDubbingAudio: ...


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

    @property
    def diagnostics(self) -> DiagnosticsTaskRuntime: ...

    @property
    def dubbing(self) -> DubbingTaskRuntime: ...
