from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TypeVar

from mediaflow.application.asset_service import AssetService
from mediaflow.application.export_catalog import default_export_preset
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.ports import TaskHandlerDocuments
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskContext, TaskService
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.enums import AssetKind, AssetOrigin, ExportFormat, TaskKind, TrackKind
from mediaflow.domain.settings import GlobalSettings, LlmProviderSettings
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeHighlightsCommand,
    AnalyzeLoudnessCommand,
    AnalyzeSequenceBoundsCommand,
    CommandModel,
    DownloadMediaCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    ImportAssetCommand,
    TranscribeAssetCommand,
    TranscribeRegionCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)
from mediaflow.infrastructure.asr_engine import create_asr_engine
from mediaflow.infrastructure.cookie_store import CookieStore
from mediaflow.infrastructure.llm_client import OpenAIJsonClient
from mediaflow.infrastructure.mlt import (
    LoudnessAnalysisService,
    MltExportService,
    SequenceBoundaryAnalysisService,
    TimelineCompiler,
)
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.translation_cache import TranslationCache
from mediaflow.infrastructure.waveform_service import WaveformService
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService

CommandT = TypeVar("CommandT", bound=CommandModel)


class ProjectTaskHandlers:
    """All background task consumers for one open project."""

    def __init__(
        self,
        documents: TaskHandlerDocuments,
        assets: AssetService,
        paths: RuntimePaths,
        cookies: CookieStore,
        subtitle_acquisition: SubtitleAcquisitionService,
        subtitle_editing: SubtitleEditingService,
        subtitle_publication: SubtitlePublicationService,
        settings: Callable[[], GlobalSettings],
        active_llm_provider: Callable[[], LlmProviderSettings],
    ):
        self.documents = documents
        self.assets = assets
        self.paths = paths
        self.cookies = cookies
        self.subtitle_acquisition = subtitle_acquisition
        self.subtitle_editing = subtitle_editing
        self.subtitle_publication = subtitle_publication
        self.settings = settings
        self.active_llm_provider = active_llm_provider
        self.highlights = HighlightService(documents, OpenAIJsonClient)
        self.translations = TranslationService(
            documents,
            OpenAIJsonClient,
            TranslationCache(documents.project_dir),
            self.subtitle_publication.write_document_srt,
        )

    def register_with(self, tasks: TaskService) -> None:
        tasks.register(TaskKind.ANALYZE, self.analyze)
        tasks.register(TaskKind.IMPORT, self.import_asset)
        tasks.register(TaskKind.PROXY, self.proxy)
        tasks.register(TaskKind.WAVEFORM, self.waveform)
        tasks.register(TaskKind.DOWNLOAD, self.download)
        tasks.register(TaskKind.EXPORT, self.export)
        tasks.register(TaskKind.TRANSCRIBE, self.transcribe)
        tasks.register(TaskKind.TRANSLATE, self.translate)
        tasks.register(TaskKind.HIGHLIGHT, self.highlight)

    @property
    def project_dir(self) -> Path:
        return self.documents.project_dir

    def import_asset(self, context: TaskContext) -> list[str]:
        command = self._command(context, ImportAssetCommand)
        context.report_progress(10, "import_probing")
        if command.purpose == "subtitle":
            document = self.subtitle_acquisition.import_subtitle_file(
                command.source_path,
                self.assets,
                language=command.language,
                media_asset_id=command.media_asset_id,
            )
            asset = self.documents.get_asset(document.asset_id)
        else:
            asset = self.assets.import_external(
                command.source_path,
                expected_kind=(AssetKind.IMAGE if command.purpose == "watermark" else None),
            )
        context.report_progress(95, "import_registering")
        return [asset.path]

    def proxy(self, context: TaskContext) -> list[str]:
        command = self._command(context, GenerateProxyCommand)
        asset = self.documents.get_asset(command.asset_id)
        sequence_id = context.task.sequence_id or self.documents.get_project().main_sequence_id
        sequence = self.documents.get_sequence(sequence_id)
        context.report_progress(10, "proxy_preparing")
        updated = ProxyService(self.documents, self.paths).generate(
            asset,
            sequence.profile,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        context.report_progress(95, "proxy_verifying")
        return [path for path in (updated.proxy_path, updated.sdr_preview_proxy_path) if path]

    def waveform(self, context: TaskContext) -> list[str]:
        command = self._command(context, GenerateWaveformCommand)
        asset = self.documents.get_asset(command.asset_id)
        context.report_progress(10, "waveform_decoding")
        updated = WaveformService(self.documents, self.paths).generate(
            asset,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        context.report_progress(95, "waveform_verifying")
        return [updated.waveform_path] if updated.waveform_path else []

    def download(self, context: TaskContext) -> list[str]:
        command = self._command(context, DownloadMediaCommand)
        settings = self.settings().download
        request = command.request
        managed_cookie = self.cookies.resolve_for_url(request.entry.page_url)
        cookie_file = settings.cookie_file or (str(managed_cookie) if managed_cookie is not None else None)
        paths = YtDlpDownloadService().download(
            request,
            cookie_file=cookie_file,
            browser_cookies=None if cookie_file else settings.browser_cookies,
            proxy=settings.proxy,
            progress=context.report_progress,
        )
        existing = {
            self.documents.resolve_asset_path(asset).resolve(): asset
            for asset in self.documents.list_assets()
        }
        assets = [
            existing.get(path.resolve()) or self.assets.register_output(path, AssetOrigin.DOWNLOAD)
            for path in paths
        ]
        for asset in assets:
            if asset.kind == AssetKind.SUBTITLE and Path(asset.path).suffix.lower() == ".srt":
                self.subtitle_acquisition.create_document_from_subtitle_asset(asset.id)
        return [asset.path for asset in assets]

    def export(self, context: TaskContext) -> list[str]:
        command = context.task.command
        if isinstance(command, ExportHighlightsCommand):
            return self._export_highlights(context, command)
        if isinstance(command, ExportSequenceCommand):
            state = self.documents.load_timeline(command.sequence_id)
            preset = command.preset or default_export_preset(
                command.format,
                state.sequence.profile.color_mode,
                state.sequence.profile.fps,
            )
            context.report_progress(5, "export_compiling")
            result = MltExportService(TimelineCompiler(self.documents), self.paths).export(
                state,
                preset,
                command.output_path,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            context.report_progress(98, "export_verifying")
            return [str(result.output_path), *(str(path) for path in result.subtitle_files)]
        raise TypeError(f"Unexpected export command: {type(command).__name__}")

    def _export_highlights(
        self,
        context: TaskContext,
        command: ExportHighlightsCommand,
    ) -> list[str]:
        output_dir = Path(command.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates = {candidate.id: candidate for candidate in self.documents.list_highlights()}
        artifacts: list[str] = []
        service = self.highlights
        exporter = MltExportService(TimelineCompiler(self.documents), self.paths)
        for index, candidate_id in enumerate(command.candidate_ids, start=1):
            context.cancellation.raise_if_requested()
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            sequence = service.create_short_sequence(candidate.id)
            state = self.documents.load_timeline(sequence.id)
            preset = command.preset or default_export_preset(
                ExportFormat.H264,
                state.sequence.profile.color_mode,
                state.sequence.profile.fps,
            )
            subtitle_track = next(
                (
                    track
                    for track in state.tracks
                    if track.kind == TrackKind.SUBTITLE and self.documents.list_subtitle_placements(track.id)
                ),
                None,
            )
            preset = preset.model_copy(
                update={
                    "burn_subtitle_track_id": (
                        subtitle_track.id if subtitle_track and command.burn_subtitles else None
                    ),
                }
            )
            safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", candidate.title).strip(" ._")
            filename = f"{index:02d}-{safe_title or 'clip'}-{candidate.id[:8]}.{preset.container}"
            context.report_progress(
                2.0 + (index - 1) / len(command.candidate_ids) * 94.0,
                "clip_exporting",
            )
            result = exporter.export(
                state,
                preset,
                output_dir / filename,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            artifacts.extend([str(result.output_path), *(str(path) for path in result.subtitle_files)])
        context.report_progress(98, "clip_export_completed")
        return artifacts

    def transcribe(self, context: TaskContext) -> list[str]:
        command = context.task.command
        acquisition = self.subtitle_acquisition
        if isinstance(command, TranscribeRegionCommand):
            return self._transcribe_region(context, command, acquisition)
        if not isinstance(command, TranscribeAssetCommand):
            raise TypeError(f"Unexpected transcribe command: {type(command).__name__}")
        document = acquisition.transcribe_asset(
            command.asset_id,
            create_asr_engine(
                self.settings().asr,
                self.paths,
                check_cancelled=context.cancellation.raise_if_requested,
            ),
            progress=context.report_progress,
        )
        self.subtitle_editing.smart_split_document(
            document.id,
            text_limit=self.settings().asr.smart_split_limit,
        )
        output = self.subtitle_publication.write_document_srt(document.id)
        return [str(output.relative_to(self.project_dir).as_posix())]

    def _transcribe_region(
        self,
        context: TaskContext,
        command: TranscribeRegionCommand,
        acquisition: SubtitleAcquisitionService,
    ) -> list[str]:
        settings = self.settings()
        prepared = acquisition.prepare_region_transcription(
            command.asset_id,
            create_asr_engine(
                settings.asr,
                self.paths,
                check_cancelled=context.cancellation.raise_if_requested,
            ),
            start_frame=command.start_frame,
            end_frame=command.end_frame,
            document_id=command.document_id,
            language=None if settings.asr.language == "auto" else settings.asr.language,
            check_cancelled=context.cancellation.raise_if_requested,
            progress=context.report_progress,
        )
        inserted = list(prepared.segments)
        if command.translate_after:
            target_language = command.target_language or settings.translation.target_language
            inserted = self.translations.translate_segments_preserving_timing(
                inserted,
                target_language=target_language,
                provider=self.active_llm_provider(),
                mode=command.mode,
                glossary=settings.translation.glossary_terms,
                progress=lambda value, code: context.report_progress(
                    95.0 + min(100.0, value) * 0.04,
                    code,
                ),
                check_cancelled=context.cancellation.raise_if_requested,
            )
            if prepared.creates_document and command.mode != "proofread":
                prepared = replace(
                    prepared,
                    document=prepared.document.model_copy(update={"language": target_language}),
                )
                inserted = [
                    segment.model_copy(update={"document_id": prepared.document.id}) for segment in inserted
                ]
        acquisition.commit_region_transcription(prepared, inserted)
        context.report_progress(100, "transcription_completed")
        output = self.subtitle_publication.write_document_srt(prepared.document.id)
        return [str(output.relative_to(self.project_dir).as_posix())]

    def translate(self, context: TaskContext) -> list[str]:
        command = context.task.command
        settings = self.settings()
        service = self.translations
        if isinstance(command, TranslateSegmentsCommand):
            service.translate_selected_in_document(
                command.document_id,
                command.segment_ids,
                target_language=command.target_language,
                provider=self.active_llm_provider(),
                mode=command.mode,
                glossary=settings.translation.glossary_terms,
                progress=context.report_progress,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            document_id = command.document_id
        elif isinstance(command, TranslateDocumentCommand):
            document = service.translate_document(
                command.document_id,
                target_language=command.target_language,
                provider=self.active_llm_provider(),
                mode=command.mode,
                glossary=settings.translation.glossary_terms,
                progress=context.report_progress,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            document_id = document.id
        else:
            raise TypeError(f"Unexpected translation command: {type(command).__name__}")
        output = self.subtitle_publication.write_document_srt(document_id)
        return [str(output.relative_to(self.project_dir).as_posix())]

    def highlight(self, context: TaskContext) -> list[str]:
        command = self._command(context, AnalyzeHighlightsCommand)
        self.highlights.analyze_document(
            command.document_id,
            provider=self.active_llm_provider(),
            progress=context.report_progress,
        )
        return []

    def analyze(self, context: TaskContext) -> list[str]:
        command = context.task.command
        if isinstance(command, AnalyzeDownloadCommand):
            return self._analyze_download(context, command)
        if isinstance(command, AnalyzeSequenceBoundsCommand):
            state = self.documents.load_timeline(command.sequence_id)
            _analysis, result_path = SequenceBoundaryAnalysisService(
                TimelineCompiler(self.documents),
                self.paths,
            ).analyze(
                state,
                expected_snapshot_hash=command.snapshot_hash,
                check_cancelled=context.cancellation.raise_if_requested,
                report_progress=context.report_progress,
            )
            return [str(result_path.relative_to(self.project_dir).as_posix())]
        if isinstance(command, AnalyzeLoudnessCommand):
            state = self.documents.load_timeline(command.sequence_id)
            _metrics, result_path = LoudnessAnalysisService(
                TimelineCompiler(self.documents),
                self.paths,
            ).analyze(
                state,
                check_cancelled=context.cancellation.raise_if_requested,
                report_progress=context.report_progress,
            )
            return [str(result_path.relative_to(self.project_dir).as_posix())]
        raise TypeError(f"Unexpected analysis command: {type(command).__name__}")

    def _analyze_download(
        self,
        context: TaskContext,
        command: AnalyzeDownloadCommand,
    ) -> list[str]:
        context.report_progress(10, "download_analyzing")
        settings = self.settings().download
        plan = YtDlpDownloadService.analyze_configured(
            command.url,
            settings=settings,
            cookies=self.cookies,
        )
        destination = self.project_dir / "cache" / "download-analysis" / f"{context.task.id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        context.report_progress(100, "download_analysis_ready")
        return [str(destination.relative_to(self.project_dir).as_posix())]

    @staticmethod
    def _command(context: TaskContext, expected: type[CommandT]) -> CommandT:
        command = context.task.command
        if not isinstance(command, expected):
            raise TypeError(
                f"Task {context.task.id} expected {expected.__name__}, got {type(command).__name__}"
            )
        return command
