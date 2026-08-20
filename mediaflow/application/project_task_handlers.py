from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.analysis_task_handlers import AnalysisTaskHandlers
from mediaflow.application.asset_service import AssetService
from mediaflow.application.asset_task_handlers import (
    AssetTaskHandlers,
    DownloadTaskHandler,
    WebRenderTaskHandler,
)
from mediaflow.application.diagnostics_task_handler import DiagnosticsBundleTaskHandler
from mediaflow.application.dubbing_task_handler import DubbingTaskHandler
from mediaflow.application.export_task_handlers import ExportTaskHandlers
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.language_task_handlers import LanguageTaskHandlers
from mediaflow.application.ports import (
    ProjectTaskDocuments,
    ProjectTaskRuntimePorts,
)
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.transcription_task_handler import (
    TranscriptionTaskHandler,
)
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.enums import TaskKind
from mediaflow.domain.settings import LlmProviderSettings, ServiceSettings


class ProjectTaskHandlers:
    """Compose focused task consumers for one open project."""

    def __init__(
        self,
        documents: ProjectTaskDocuments,
        assets: AssetService,
        runtimes: ProjectTaskRuntimePorts,
        subtitle_acquisition: SubtitleAcquisitionService,
        subtitle_editing: SubtitleEditingService,
        subtitle_publication: SubtitlePublicationService,
        highlights: HighlightService,
        translations: TranslationService,
        settings: Callable[[], ServiceSettings],
        active_llm_provider: Callable[[], LlmProviderSettings],
        timeline_provider: Callable[[str], TimelineEditor],
    ):
        self._web = WebRenderTaskHandler(documents, runtimes.web)
        self._assets = AssetTaskHandlers(
            documents,
            assets,
            runtimes.assets,
            subtitle_acquisition,
        )
        self._downloads = DownloadTaskHandler(
            documents,
            assets,
            runtimes.downloads,
            subtitle_acquisition,
            settings,
        )
        self._exports = ExportTaskHandlers(
            documents,
            runtimes.exports,
            highlights,
        )
        self._transcription = TranscriptionTaskHandler(
            documents,
            assets,
            runtimes.transcription,
            subtitle_acquisition,
            subtitle_editing,
            subtitle_publication,
        )
        self._language = LanguageTaskHandlers(
            documents.project_dir,
            subtitle_publication,
            highlights,
            translations,
            settings,
            active_llm_provider,
        )
        self._analysis = AnalysisTaskHandlers(
            documents,
            runtimes.analysis,
            settings,
            timeline_provider,
        )
        self._diagnostics = DiagnosticsBundleTaskHandler(
            documents.project_dir,
            runtimes.diagnostics,
        )
        self._dubbing = DubbingTaskHandler(
            documents,
            assets,
            runtimes.dubbing,
            translations,
            settings,
            active_llm_provider,
            timeline_provider,
        )

    def register_with(self, tasks: TaskService) -> None:
        tasks.register(TaskKind.ANALYZE, self._analysis.handle)
        tasks.register(TaskKind.IMPORT, self._assets.import_asset)
        tasks.register(TaskKind.PROXY, self._assets.proxy)
        tasks.register(TaskKind.WAVEFORM, self._assets.waveform)
        tasks.register(TaskKind.DOWNLOAD, self._downloads.handle)
        tasks.register(TaskKind.EXPORT, self._exports.handle)
        tasks.register(TaskKind.TRANSCRIBE, self._transcription.handle)
        tasks.register(TaskKind.TRANSLATE, self._language.translate)
        tasks.register(TaskKind.HIGHLIGHT, self._language.highlight)
        tasks.register(TaskKind.WEB_RENDER, self._web.handle)
        tasks.register(TaskKind.DIAGNOSTICS, self._diagnostics.handle)
        tasks.register(TaskKind.DUBBING, self._dubbing.handle)
        tasks.recover_claimable()
