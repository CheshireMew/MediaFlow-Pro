from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mediaflow.application.ports import TaskHandlerDocuments
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset, ProjectProfile
from mediaflow.domain.settings import AsrSettings, DownloadSettings
from mediaflow.domain.timeline import Clip, TimelineState

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
from .proxy_service import ProxyService
from .runtime_paths import RuntimePaths
from .visual_analysis import (
    SceneDetectionService,
    SubjectMotionService,
    write_visual_analysis,
)
from .waveform_service import WaveformService
from .web_render_service import WebRenderService
from .ytdlp_service import YtDlpDownloadService


class InfrastructureTaskRuntime:
    """Concrete media/tool adapters behind the application task boundary."""

    def __init__(
        self,
        documents: TaskHandlerDocuments,
        paths: RuntimePaths,
        cookies: CookieStore,
    ):
        self._documents = documents
        self._paths = paths
        self._cookies = cookies
        self._web = WebRenderService(documents, paths)

    def render_web_export(self, *args: Any, **kwargs: Any) -> Any:
        return self._web.export_clip(*args, **kwargs)

    def render_web_clip(self, *args: Any, **kwargs: Any) -> Path:
        return self._web.render_clip(*args, **kwargs)

    def ensure_web_sequence(self, *args: Any, **kwargs: Any) -> None:
        self._web.ensure_sequence(*args, **kwargs)

    def generate_proxy(
        self,
        asset: Asset,
        profile: ProjectProfile,
        **kwargs: Any,
    ) -> Asset:
        return ProxyService(self._documents, self._paths).generate(asset, profile, **kwargs)

    def generate_waveform(self, asset: Asset, **kwargs: Any) -> Asset:
        return WaveformService(self._documents, self._paths).generate(asset, **kwargs)

    def download_media(
        self,
        request: Any,
        settings: DownloadSettings,
        *,
        progress: Callable[[OperationProgress], None],
    ) -> list[Path]:
        managed_cookie = self._cookies.resolve_for_url(request.entry.page_url)
        cookie_file = settings.cookie_file or (
            str(managed_cookie) if managed_cookie is not None else None
        )
        return YtDlpDownloadService().download(
            request,
            cookie_file=cookie_file,
            browser_cookies=None if cookie_file else settings.browser_cookies,
            proxy=settings.proxy,
            progress=progress,
        )

    def export_sequence(self, *args: Any, **kwargs: Any) -> Any:
        return MltExportService(TimelineCompiler(self._documents), self._paths).export(
            *args,
            **kwargs,
        )

    def analyze_export_quality(
        self,
        state: TimelineState,
        preset: Any,
        result: Any,
        **kwargs: Any,
    ) -> tuple[Any, Path]:
        return ExportQualityService(
            self._documents.project_dir,
            self._paths,
        ).analyze(state, preset, result, **kwargs)

    def create_asr_pipeline(
        self,
        settings: AsrSettings,
        **kwargs: Any,
    ) -> Any:
        return create_asr_pipeline(settings, self._paths, **kwargs)

    @staticmethod
    def fingerprint_matches(*args: Any, **kwargs: Any) -> bool:
        return fingerprint_matches(*args, **kwargs)

    @staticmethod
    def fingerprint_file(path: Path):
        return fingerprint_file(path)

    def analyze_sequence_bounds(self, state: TimelineState, **kwargs: Any) -> tuple[Any, Path]:
        return SequenceBoundaryAnalysisService(
            TimelineCompiler(self._documents),
            self._paths,
        ).analyze(state, **kwargs)

    def analyze_loudness(self, state: TimelineState, **kwargs: Any) -> tuple[Any, Path]:
        return LoudnessAnalysisService(
            TimelineCompiler(self._documents),
            self._paths,
        ).analyze(state, **kwargs)

    def detect_scenes(
        self,
        source: Path,
        clip: Clip,
        profile: ProjectProfile,
        **kwargs: Any,
    ) -> list[int]:
        return SceneDetectionService(self._paths).detect(source, clip, profile, **kwargs)

    @staticmethod
    def track_subject(
        source: Path,
        clip: Clip,
        profile: ProjectProfile,
        **kwargs: Any,
    ) -> list[Any]:
        return SubjectMotionService().analyze(source, clip, profile, **kwargs)

    @staticmethod
    def write_visual_analysis(path: Path, payload: dict[str, Any]) -> Path:
        return write_visual_analysis(path, payload)

    def analyze_download(self, url: str, settings: DownloadSettings) -> Any:
        return YtDlpDownloadService.analyze_configured(
            url,
            settings=settings,
            cookies=self._cookies,
        )
