from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from mediaflow.application.ports import (
    AnalysisTaskRuntime,
    AssetTaskRuntime,
    DownloadTaskRuntime,
    ExportExecutionResult,
    ExportSequenceRequest,
    ExportTaskRuntime,
    ProjectTaskDocuments,
    TranscriptionTaskRuntime,
    WebTaskRuntime,
)
from mediaflow.domain.asr import RegionAsrPipeline
from mediaflow.domain.downloads import DownloadPlan, DownloadRequest
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset, AssetFingerprint, ProjectProfile
from mediaflow.domain.project_records import ExportQualityReport
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.settings import AsrSettings, DownloadSettings
from mediaflow.domain.storage_names import (
    export_quality_directory,
    safe_child_path,
)
from mediaflow.domain.tasks import LoudnessTaskOutcome
from mediaflow.domain.timeline import Clip, ClipTransformKeyframe, TimelineState
from mediaflow.domain.web_media import (
    WebClipExportResult,
    WebExportFormat,
)

from .asr_engine import create_asr_pipeline
from .cookie_store import CookieStore
from .export_quality import ExportQualityService
from .file_fingerprint import fingerprint_file, fingerprint_matches
from .mlt import (
    LoudnessAnalysisService,
    MltExportService,
    SequenceBoundaryAnalysisService,
    TimelineCompiler,
)
from .mlt.export_service import (
    ExportResult,
    MltExportRequest,
)
from .output_reservation import archive_published_outputs
from .proxy_service import ProxyService
from .runtime_paths import RuntimePaths
from .visual_analysis import (
    SceneDetectionService,
    SubjectMotionService,
    write_visual_analysis,
)
from .waveform_service import WaveformService
from .web_render_service import WebRenderService
from .ytdlp_service import (
    FAILED_DOWNLOAD_DIRECTORY_NAME,
    YtDlpDownloadService,
)

ProgressCallback = Callable[[OperationProgress], None]
CancellationCheck = Callable[[], None]


class InfrastructureWebTaskRuntime:
    def __init__(
        self,
        documents: ProjectTaskDocuments,
        paths: RuntimePaths,
    ):
        self._service = WebRenderService(documents, paths)

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
    ) -> WebClipExportResult:
        return self._service.export_clip(
            state,
            clip_id,
            output_path,
            format,
            time_ms=time_ms,
            background=background,
            overwrite=overwrite,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    def render_web_clip(
        self,
        state: TimelineState,
        clip_id: str,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> Path:
        return self._service.render_clip(
            state,
            clip_id,
            progress=progress,
            check_cancelled=check_cancelled,
        )


class InfrastructureAssetTaskRuntime:
    def __init__(
        self,
        documents: ProjectTaskDocuments,
        paths: RuntimePaths,
    ):
        self._documents = documents
        self._paths = paths

    def generate_proxy(
        self,
        asset: Asset,
        profile: ProjectProfile,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> Asset:
        return ProxyService(self._documents, self._paths).generate(
            asset,
            profile,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    def generate_waveform(
        self,
        asset: Asset,
        *,
        duration_seconds: float,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> Asset:
        return WaveformService(self._documents, self._paths).generate(
            asset,
            duration_seconds=duration_seconds,
            progress=progress,
            check_cancelled=check_cancelled,
        )


class InfrastructureDownloadTaskRuntime:
    def __init__(self, cookies: CookieStore):
        self._cookies = cookies

    def download_media(
        self,
        request: DownloadRequest,
        settings: DownloadSettings,
        *,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> list[Path]:
        managed_cookie = self._cookies.resolve_for_url(request.entry.page_url)
        cookie_file = settings.cookie_file or (str(managed_cookie) if managed_cookie is not None else None)
        return YtDlpDownloadService().download(
            request,
            cookie_file=cookie_file,
            browser_cookies=(None if cookie_file else settings.browser_cookies),
            proxy=settings.proxy,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    def archive_unrecorded_downloads(
        self,
        paths: list[Path],
    ) -> tuple[Path, ...]:
        return archive_published_outputs(
            paths,
            archive_directory_name=(
                FAILED_DOWNLOAD_DIRECTORY_NAME
            ),
        )


class InfrastructureExportTaskRuntime:
    def __init__(
        self,
        documents: ProjectTaskDocuments,
        paths: RuntimePaths,
    ):
        self._documents = documents
        self._paths = paths
        self._web = WebRenderService(documents, paths)

    def preflight_sequence_exports(
        self,
        requests: list[ExportSequenceRequest],
        *,
        overwrite: bool,
    ) -> None:
        MltExportService(
            TimelineCompiler(self._documents),
            self._paths,
        ).preflight_many(
            self._mlt_requests(requests),
            overwrite=overwrite,
        )

    def export_sequence(
        self,
        state: TimelineState,
        preset: ExportPreset,
        output_path: str | Path,
        *,
        overwrite: bool,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> ExportResult:
        return cast(
            ExportResult,
            self.export_sequences_atomically(
                [
                    ExportSequenceRequest(
                        state=state,
                        preset=preset,
                        output_path=output_path,
                    )
                ],
                overwrite=overwrite,
                progress=progress,
                check_cancelled=check_cancelled,
            )[0],
        )

    def export_sequences_atomically(
        self,
        requests: list[ExportSequenceRequest],
        *,
        overwrite: bool,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> tuple[ExportExecutionResult, ...]:
        exporter = MltExportService(
            TimelineCompiler(self._documents),
            self._paths,
        )
        mlt_requests = self._mlt_requests(requests)
        exporter.preflight_many(
            mlt_requests,
            overwrite=overwrite,
        )
        for request in requests:
            check_cancelled()
            self._web.ensure_sequence(
                request.state,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        check_cancelled()
        return exporter.export_many(
            mlt_requests,
            overwrite=overwrite,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    def archive_unrecorded_exports(
        self,
        results: list[ExportExecutionResult],
        *,
        quality_report_id: str | None = None,
    ) -> tuple[Path, ...]:
        destinations = [
            destination
            for result in results
            for destination in (
                result.output_path,
                *result.subtitle_files,
            )
        ]
        archived = list(
            archive_published_outputs(
                destinations,
                runtime_dir=self._paths.runtime_dir,
            )
        )
        if quality_report_id is not None:
            report_dir = export_quality_directory(
                self._documents.project_dir,
                quality_report_id,
            )
            if report_dir.exists():
                if not report_dir.is_dir():
                    raise RuntimeError(
                        f"Export quality evidence is not a directory: {report_dir}"
                    )
                archive_root = (
                    report_dir.parent / "MediaFlow Failed Export QA"
                )
                attempt = 1
                while True:
                    target = safe_child_path(
                        archive_root,
                        report_dir.name,
                        suffix="" if attempt == 1 else f"-{attempt}",
                        fallback="qa",
                    )
                    if not target.exists():
                        break
                    attempt += 1
                target.parent.mkdir(parents=True, exist_ok=True)
                report_dir.replace(target)
                archived.append(target)
        return tuple(archived)

    @staticmethod
    def _mlt_requests(
        requests: list[ExportSequenceRequest],
    ) -> tuple[MltExportRequest, ...]:
        return tuple(
            MltExportRequest(
                state=request.state,
                preset=request.preset,
                output_path=request.output_path,
            )
            for request in requests
        )

    def analyze_export_quality(
        self,
        state: TimelineState,
        preset: ExportPreset,
        result: ExportExecutionResult,
        *,
        report_id: str,
        progress: ProgressCallback,
        check_cancelled: CancellationCheck,
    ) -> tuple[ExportQualityReport, Path]:
        return ExportQualityService(
            self._documents.project_dir,
            self._paths,
        ).analyze(
            state,
            preset,
            cast(ExportResult, result),
            report_id=report_id,
            progress=progress,
            check_cancelled=check_cancelled,
        )


class InfrastructureTranscriptionTaskRuntime:
    def __init__(self, paths: RuntimePaths):
        self._paths = paths

    def create_asr_pipeline(
        self,
        settings: AsrSettings,
        *,
        check_cancelled: CancellationCheck,
    ) -> RegionAsrPipeline:
        return create_asr_pipeline(
            settings,
            self._paths,
            check_cancelled=check_cancelled,
        )

    @staticmethod
    def fingerprint_matches(
        path: Path,
        fingerprint: AssetFingerprint,
    ) -> bool:
        return fingerprint_matches(path, fingerprint)

    @staticmethod
    def fingerprint_file(path: Path) -> AssetFingerprint:
        return fingerprint_file(path)


class InfrastructureAnalysisTaskRuntime:
    def __init__(
        self,
        documents: ProjectTaskDocuments,
        paths: RuntimePaths,
        cookies: CookieStore,
    ):
        self._documents = documents
        self._paths = paths
        self._cookies = cookies

    def analyze_sequence_bounds(
        self,
        state: TimelineState,
        *,
        expected_snapshot_hash: str,
        check_cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> tuple[SequenceBoundaryAnalysis, Path]:
        return SequenceBoundaryAnalysisService(
            TimelineCompiler(self._documents),
            self._paths,
        ).analyze(
            state,
            expected_snapshot_hash=expected_snapshot_hash,
            check_cancelled=check_cancelled,
            progress=progress,
        )

    def analyze_loudness(
        self,
        state: TimelineState,
        *,
        check_cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> tuple[LoudnessTaskOutcome, Path]:
        metrics, path = LoudnessAnalysisService(
            TimelineCompiler(self._documents),
            self._paths,
        ).analyze(
            state,
            check_cancelled=check_cancelled,
            progress=progress,
        )
        return (
            LoudnessTaskOutcome(
                sample_peak_dbfs=metrics.sample_peak_dbfs,
                true_peak_dbtp=metrics.true_peak_dbtp,
                short_term_lufs=metrics.short_term_lufs,
                integrated_lufs=metrics.integrated_lufs,
            ),
            path,
        )

    def detect_scenes(
        self,
        source: Path,
        clip: Clip,
        profile: ProjectProfile,
        *,
        threshold: float,
        check_cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> list[int]:
        return SceneDetectionService(self._paths).detect(
            source,
            clip,
            profile,
            threshold=threshold,
            check_cancelled=check_cancelled,
            progress=progress,
        )

    @staticmethod
    def track_subject(
        source: Path,
        clip: Clip,
        profile: ProjectProfile,
        *,
        mode: Literal["auto_reframe", "subject_tracking"],
        check_cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> list[ClipTransformKeyframe]:
        return SubjectMotionService().analyze(
            source,
            clip,
            profile,
            mode=mode,
            check_cancelled=check_cancelled,
            progress=progress,
        )

    @staticmethod
    def write_visual_analysis(
        path: Path,
        payload: dict[str, Any],
    ) -> Path:
        return write_visual_analysis(path, payload)

    def analyze_download(
        self,
        url: str,
        settings: DownloadSettings,
        *,
        check_cancelled: CancellationCheck,
    ) -> DownloadPlan:
        return YtDlpDownloadService.analyze_configured(
            url,
            settings=settings,
            cookies=self._cookies,
            check_cancelled=check_cancelled,
        )


@dataclass(frozen=True, slots=True)
class InfrastructureTaskRuntimes:
    web: WebTaskRuntime
    assets: AssetTaskRuntime
    downloads: DownloadTaskRuntime
    exports: ExportTaskRuntime
    transcription: TranscriptionTaskRuntime
    analysis: AnalysisTaskRuntime

    @classmethod
    def create(
        cls,
        documents: ProjectTaskDocuments,
        paths: RuntimePaths,
        cookies: CookieStore,
    ) -> InfrastructureTaskRuntimes:
        return cls(
            web=InfrastructureWebTaskRuntime(documents, paths),
            assets=InfrastructureAssetTaskRuntime(documents, paths),
            downloads=InfrastructureDownloadTaskRuntime(cookies),
            exports=InfrastructureExportTaskRuntime(documents, paths),
            transcription=InfrastructureTranscriptionTaskRuntime(paths),
            analysis=InfrastructureAnalysisTaskRuntime(
                documents,
                paths,
                cookies,
            ),
        )
