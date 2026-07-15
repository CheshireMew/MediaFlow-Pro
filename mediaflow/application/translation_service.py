from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.subtitle_service import SubtitleService
from mediaflow.domain.models import SubtitleDocument, SubtitleSegment
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.infrastructure.llm_client import OpenAIJsonClient
from mediaflow.infrastructure.project_repository import ProjectRepository

TranslationProgress = Callable[[float, str], None]


class TranslationService:
    BATCH_SIZE = 60

    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    def translate_document(
        self,
        document_id: str,
        *,
        target_language: str,
        provider: LlmProviderSettings,
        glossary: dict[str, str] | None = None,
        progress: TranslationProgress | None = None,
    ) -> SubtitleDocument:
        source_document = self.repository.get_subtitle_document(document_id)
        source_segments = self.repository.list_subtitle_segments(document_id)
        if not source_segments:
            raise ValueError("Source subtitle document is empty")
        client = OpenAIJsonClient(provider)
        translations: dict[str, str] = {}
        for offset in range(0, len(source_segments), self.BATCH_SIZE):
            batch = source_segments[offset : offset + self.BATCH_SIZE]
            response = client.complete_json(
                system=(
                    "Translate subtitle segments to the requested language. "
                    "Preserve every id and order. Do not merge or split segments. "
                    'Return only JSON: {"segments":[{"id":"...","text":"..."}]}'
                ),
                payload={
                    "target_language": target_language,
                    "glossary": glossary or {},
                    "segments": [{"id": item.id, "text": item.text} for item in batch],
                },
            )
            translated = response.get("segments")
            if not isinstance(translated, list):
                raise RuntimeError("Translation response is missing segments")
            expected_ids = [item.id for item in batch]
            received_ids = [str(item.get("id")) for item in translated if isinstance(item, dict)]
            if received_ids != expected_ids:
                raise RuntimeError("Translation response changed segment IDs or order")
            for item in translated:
                text = str(item.get("text") or "").strip()
                if not text:
                    raise RuntimeError(f"Translation is empty for segment {item.get('id')}")
                translations[str(item["id"])] = text
            if progress:
                progress(
                    min(98.0, (offset + len(batch)) / len(source_segments) * 98.0),
                    "translating",
                )

        project = self.repository.get_project()
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=source_document.asset_id,
            language=target_language,
            source_document_id=source_document.id,
            is_source=False,
        )
        segments = [
            SubtitleSegment(
                document_id=document.id,
                source_segment_id=source.id,
                start_frame=source.start_frame,
                end_frame=source.end_frame,
                text=translations[source.id],
                speaker=source.speaker,
                confidence=source.confidence,
            )
            for source in source_segments
        ]
        self.repository.create_subtitle_document(document, segments)
        SubtitleService(self.repository).write_document_srt(document.id)
        if progress:
            progress(100.0, "translation_completed")
        return document
