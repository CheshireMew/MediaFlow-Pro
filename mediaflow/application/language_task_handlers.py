from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.settings import GlobalSettings, LlmProviderSettings
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
        settings: Callable[[], GlobalSettings],
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
                self.translations.translate_selected_to_document(
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
                self.translations.translate_selected_in_document(
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
        elif isinstance(command, TranslateDocumentCommand):
            document = self.translations.translate_document(
                command.document_id,
                target_language=command.target_language,
                provider=self.active_llm_provider(),
                mode=command.mode,
                glossary=settings.translation.glossary_terms,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
                operation_id=context.task.id,
            )
            document_id = document.id
        else:
            raise TypeError(f"Unexpected translation command: {type(command).__name__}")
        output = self.subtitle_publication.document_srt_path(document_id)
        return self.completion(output)

    def highlight(self, context: TaskContext) -> TaskCompletion:
        command = self.command(context, AnalyzeHighlightsCommand)
        self.highlights.analyze_document(
            command.document_id,
            provider=self.active_llm_provider(),
            progress=context.report,
        )
        return self.completion()
