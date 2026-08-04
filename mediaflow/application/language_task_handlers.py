from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.settings import LlmProviderSettings, ServiceSettings
from mediaflow.domain.task_commands import (
    AnalyzeHighlightsCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)


class LanguageTaskHandlers(ProjectTaskHandler):
    def __init__(
        self,
        project_dir,
        subtitle_publication: SubtitlePublicationService,
        highlights: HighlightService,
        translations: TranslationService,
        settings: Callable[[], ServiceSettings],
        active_llm_provider: Callable[[], LlmProviderSettings],
    ):
        super().__init__(project_dir)
        self.subtitle_publication = subtitle_publication
        self.highlights = highlights
        self.translations = translations
        self.settings = settings
        self.active_llm_provider = active_llm_provider

    def translate(self, context: TaskContext) -> TaskCompletion:
        command = context.task.command
        settings = self.settings()
        if isinstance(command, TranslateSegmentsCommand):
            if command.target_document_id:
                prepared = self.translations.prepare_selected_to_document_translation(
                    command.document_id,
                    command.target_document_id,
                    command.segment_ids,
                    target_language=command.target_language,
                    provider=self.active_llm_provider(),
                    mode=command.mode,
                    glossary=settings.translation.glossary_terms,
                    progress=context.report,
                    check_cancelled=context.cancellation.raise_if_requested,
                )
                document_id = command.target_document_id
            else:
                prepared = self.translations.prepare_selected_in_document_translation(
                    command.document_id,
                    command.segment_ids,
                    target_language=command.target_language,
                    provider=self.active_llm_provider(),
                    mode=command.mode,
                    glossary=settings.translation.glossary_terms,
                    progress=context.report,
                    check_cancelled=context.cancellation.raise_if_requested,
                )
                document_id = command.document_id

            def commit_translation() -> None:
                self.translations.commit_segment_translation(prepared)

        elif isinstance(command, TranslateDocumentCommand):
            prepared_document = self.translations.prepare_document_translation(
                command.document_id,
                target_language=command.target_language,
                provider=self.active_llm_provider(),
                mode=command.mode,
                glossary=settings.translation.glossary_terms,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
                operation_id=context.task.id,
            )
            document_id = prepared_document.document.id

            def commit_translation() -> None:
                self.translations.commit_document_translation(
                    prepared_document
                )

        else:
            raise TypeError(f"Unexpected translation command: {type(command).__name__}")
        context.defer_project_change(commit_translation)
        output = self.subtitle_publication.document_srt_path(document_id)
        return self.completion(output)

    def highlight(self, context: TaskContext) -> TaskCompletion:
        command = self.command(context, AnalyzeHighlightsCommand)
        prepared = self.highlights.prepare_document_analysis(
            command.document_id,
            provider=self.active_llm_provider(),
            progress=context.report,
        )

        def commit_highlights() -> None:
            self.highlights.commit_document_analysis(
                prepared,
                progress=context.report,
            )

        context.defer_project_change(commit_highlights)
        return self.completion()
