from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mediaflow.application.ports import (
    JsonClientFactory,
    TranslationCachePort,
    TranslationDocuments,
)
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.subtitle_translation_strategy import SubtitleTranslationStrategy
from mediaflow.application.translation_execution import (
    TranslationBatch,
    TranslationBatchExecutor,
    check_translation_cancelled,
)
from mediaflow.domain.model_base import new_id
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import GlossaryTermSettings, LlmProviderSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.translation import TranslationMode, validate_translation_mode

TranslationProgress = Callable[[OperationProgress], None]


@dataclass(frozen=True, slots=True)
class PreparedDocumentTranslation:
    document: SubtitleDocument
    exists: bool
    segments: tuple[SubtitleSegment, ...]
    source_document: SubtitleDocument
    source_segments: tuple[SubtitleSegment, ...]
    target_document: SubtitleDocument | None
    target_segments: tuple[SubtitleSegment, ...]


@dataclass(frozen=True, slots=True)
class PreparedSegmentTranslation:
    document_id: str
    baseline: tuple[SubtitleSegment, ...]
    segments: tuple[SubtitleSegment, ...]
    result: tuple[SubtitleSegment, ...]
    source_document_id: str
    source_segments: tuple[SubtitleSegment, ...]


class TranslationService:
    def __init__(
        self,
        repository: TranslationDocuments,
        client_factory: JsonClientFactory,
        cache: TranslationCachePort,
        publication: SubtitlePublicationService,
    ):
        self.repository = repository
        self._strategy = SubtitleTranslationStrategy(client_factory, cache)
        self._batches = TranslationBatchExecutor()
        self.publication = publication

    def prepare_document_translation(
        self,
        document_id: str,
        *,
        target_language: str,
        provider: LlmProviderSettings,
        mode: TranslationMode = "standard",
        glossary: list[GlossaryTermSettings] | None = None,
        progress: TranslationProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
        operation_id: str | None = None,
    ) -> PreparedDocumentTranslation:
        mode = validate_translation_mode(mode)
        source_document = self.repository.subtitles.get_subtitle_document(document_id)
        source_segments = self.repository.subtitles.list_subtitle_segments(document_id)
        if not source_segments:
            raise ValueError("Source subtitle document is empty")
        output_language = source_document.language if mode == "proofread" else target_language.strip()
        if not output_language:
            raise ValueError("Target language is required")

        project = self.repository.projects.get_project()
        try:
            existing = self.repository.subtitles.get_subtitle_document(operation_id) if operation_id else None
        except KeyError:
            existing = None
        if existing is not None and (
            existing.is_source
            or existing.source_document_id != source_document.id
            or existing.language != output_language
        ):
            raise RuntimeError("Translation operation id belongs to another result")
        target_segments = (
            tuple(self.repository.subtitles.list_subtitle_segments(existing.id))
            if existing is not None else ()
        )
        document = (
            existing.model_copy(
                update={
                    "asset_id": source_document.asset_id,
                    "media_asset_id": source_document.media_asset_id,
                    "sequence_id": source_document.sequence_id,
                }
            )
            if existing is not None
            else SubtitleDocument(
                id=operation_id or new_id(),
                project_id=project.id,
                asset_id=source_document.asset_id,
                media_asset_id=source_document.media_asset_id,
                sequence_id=source_document.sequence_id,
                language=output_language,
                source_document_id=source_document.id,
                is_source=False,
            )
        )
        batches = self._batches.build(source_segments, mode)

        def translate_batch(batch: TranslationBatch) -> list[SubtitleSegment]:
            relevant_terms = self._strategy.relevant_glossary(list(batch.segments), glossary or [])
            if mode == "intelligent":
                return self._strategy.translate_intelligent_batch(
                    provider,
                    list(batch.segments),
                    document_id=document.id,
                    target_language=output_language,
                    glossary=relevant_terms,
                )
            translated = self._strategy.translate_strict_batch(
                provider,
                list(batch.segments),
                target_language=output_language,
                mode=mode,
                glossary=relevant_terms,
                context_before=list(batch.context_before),
                check_cancelled=check_cancelled,
            )
            return [
                SubtitleSegment(
                    document_id=document.id,
                    source_segment_id=source.id,
                    start_frame=source.start_frame,
                    end_frame=source.end_frame,
                    text=translated[source.id],
                    speaker=source.speaker,
                    confidence=source.confidence,
                )
                for source in batch.segments
            ]

        output_segments = self._batches.run(
            batches,
            translate_batch,
            progress=progress,
            message="proofreading" if mode == "proofread" else "translating",
            check_cancelled=check_cancelled,
        )

        check_translation_cancelled(check_cancelled)
        if progress:
            progress(OperationProgress.indeterminate("translation_saving"))

        return PreparedDocumentTranslation(
            document=document,
            exists=existing is not None,
            segments=tuple(output_segments),
            source_document=source_document,
            source_segments=tuple(source_segments),
            target_document=existing,
            target_segments=target_segments,
        )

    def commit_document_translation(
        self,
        prepared: PreparedDocumentTranslation,
    ) -> SubtitleDocument:
        def save_translation() -> None:
            if (
                self.repository.subtitles.get_subtitle_document(prepared.source_document.id)
                != prepared.source_document
                or tuple(self.repository.subtitles.list_subtitle_segments(prepared.source_document.id))
                != prepared.source_segments
            ):
                raise RuntimeError("翻译期间源字幕已修改，未提交旧译文，请重新翻译")
            if prepared.exists and (
                self.repository.subtitles.get_subtitle_document(prepared.document.id)
                != prepared.target_document
                or tuple(self.repository.subtitles.list_subtitle_segments(prepared.document.id))
                != prepared.target_segments
            ):
                raise RuntimeError("翻译期间目标字幕已修改，未覆盖你的修改，请重新翻译")
            if not prepared.exists:
                self.repository.subtitles.create_subtitle_document(
                    prepared.document,
                    list(prepared.segments),
                )
            else:
                self.repository.subtitles.save_subtitle_document(prepared.document)
                self.repository.subtitles.save_subtitle_segments(
                    prepared.document.id,
                    list(prepared.segments),
                )

        self.publication.commit_document_change(
            prepared.document.id,
            save_translation,
        )
        return prepared.document

    def translate_document(
        self,
        document_id: str,
        *,
        target_language: str,
        provider: LlmProviderSettings,
        mode: TranslationMode = "standard",
        glossary: list[GlossaryTermSettings] | None = None,
        progress: TranslationProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
        operation_id: str | None = None,
    ) -> SubtitleDocument:
        return self.commit_document_translation(
            self.prepare_document_translation(
                document_id,
                target_language=target_language,
                provider=provider,
                mode=mode,
                glossary=glossary,
                progress=progress,
                check_cancelled=check_cancelled,
                operation_id=operation_id,
            )
        )

    def translate_segments_preserving_timing(
        self,
        segments: list[SubtitleSegment],
        *,
        target_language: str,
        provider: LlmProviderSettings,
        mode: TranslationMode = "standard",
        glossary: list[GlossaryTermSettings] | None = None,
        progress: TranslationProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> list[SubtitleSegment]:
        mode = validate_translation_mode(mode)
        if not segments:
            raise ValueError("Source subtitle segments are empty")
        language = target_language.strip()
        if mode != "proofread" and not language:
            raise ValueError("Target language is required")
        batches = self._batches.build(segments, mode)

        def translate_batch(batch: TranslationBatch) -> list[SubtitleSegment]:
            translated = self._strategy.translate_strict_batch(
                provider,
                list(batch.segments),
                target_language=language,
                mode=mode,
                glossary=self._strategy.relevant_glossary(list(batch.segments), glossary or []),
                context_before=list(batch.context_before),
                check_cancelled=check_cancelled,
            )
            return [
                source.model_copy(update={"text": translated[source.id], "confidence": None})
                for source in batch.segments
            ]

        return self._batches.run(
            batches,
            translate_batch,
            progress=progress,
            message="proofreading" if mode == "proofread" else "translating",
            check_cancelled=check_cancelled,
        )

    def prepare_selected_in_document_translation(
        self,
        document_id: str,
        segment_ids: list[str],
        *,
        target_language: str,
        provider: LlmProviderSettings,
        mode: TranslationMode = "standard",
        glossary: list[GlossaryTermSettings] | None = None,
        progress: TranslationProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PreparedSegmentTranslation:
        wanted = set(segment_ids)
        if not wanted:
            raise ValueError("请先选择要翻译的字幕段")
        all_segments = self.repository.subtitles.list_subtitle_segments(document_id)
        selected = [segment for segment in all_segments if segment.id in wanted]
        if len(selected) != len(wanted):
            raise KeyError("包含不属于当前字幕文档的字幕段")
        translated = self.translate_segments_preserving_timing(
            selected,
            target_language=target_language,
            provider=provider,
            mode=mode,
            glossary=glossary,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        replacements = {segment.id: segment for segment in translated}
        check_translation_cancelled(check_cancelled)
        return PreparedSegmentTranslation(
            document_id=document_id,
            baseline=tuple(all_segments),
            segments=tuple(replacements.get(segment.id, segment) for segment in all_segments),
            result=tuple(translated),
            source_document_id=document_id,
            source_segments=tuple(selected),
        )

    def translate_selected_in_document(
        self,
        document_id: str,
        segment_ids: list[str],
        *,
        target_language: str,
        provider: LlmProviderSettings,
        mode: TranslationMode = "standard",
        glossary: list[GlossaryTermSettings] | None = None,
        progress: TranslationProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> list[SubtitleSegment]:
        return self.commit_segment_translation(
            self.prepare_selected_in_document_translation(
                document_id,
                segment_ids,
                target_language=target_language,
                provider=provider,
                mode=mode,
                glossary=glossary,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        )

    def prepare_selected_to_document_translation(
        self,
        source_document_id: str,
        target_document_id: str,
        segment_ids: list[str],
        *,
        target_language: str,
        provider: LlmProviderSettings,
        mode: TranslationMode = "standard",
        glossary: list[GlossaryTermSettings] | None = None,
        progress: TranslationProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PreparedSegmentTranslation:
        source_document = self.repository.subtitles.get_subtitle_document(source_document_id)
        target_document = self.repository.subtitles.get_subtitle_document(target_document_id)
        if target_document.source_document_id != source_document.id:
            raise ValueError("目标字幕文档不属于当前源文档")
        wanted = set(segment_ids)
        source_segments = self.repository.subtitles.list_subtitle_segments(source_document_id)
        selected = [segment for segment in source_segments if segment.id in wanted]
        if not selected or len(selected) != len(wanted):
            raise KeyError("包含不属于当前源字幕文档的字幕段")
        # Capture the target before the network call, so edits made while the
        # provider runs are checked against the actual request baseline.
        existing = self.repository.subtitles.list_subtitle_segments(target_document_id)
        translated = self.translate_segments_preserving_timing(
            selected,
            target_language=target_language,
            provider=provider,
            mode=mode,
            glossary=glossary,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        selected_ranges = [(segment.start_frame, segment.end_frame) for segment in selected]
        by_source = {
            segment.source_segment_id: segment
            for segment in existing
            if segment.source_segment_id is not None
        }
        replacements: dict[str, SubtitleSegment] = {}
        additions: list[SubtitleSegment] = []
        for translated_segment in translated:
            current = by_source.get(translated_segment.id)
            if current is None:
                additions.append(
                    SubtitleSegment(
                        document_id=target_document_id,
                        source_segment_id=translated_segment.id,
                        start_frame=translated_segment.start_frame,
                        end_frame=translated_segment.end_frame,
                        text=translated_segment.text,
                        speaker=translated_segment.speaker,
                        confidence=None,
                    )
                )
            else:
                replacements[current.id] = current.model_copy(
                    update={
                        "start_frame": translated_segment.start_frame,
                        "end_frame": translated_segment.end_frame,
                        "text": translated_segment.text,
                        "confidence": None,
                    }
                )

        def keep_existing(segment: SubtitleSegment) -> bool:
            if segment.id in replacements:
                return True
            if segment.source_segment_id is not None:
                return True
            return not any(
                segment.end_frame > start_frame and segment.start_frame < end_frame
                for start_frame, end_frame in selected_ranges
            )

        updated = [
            replacements.get(segment.id, segment) for segment in existing if keep_existing(segment)
        ] + additions
        updated.sort(key=lambda segment: (segment.start_frame, segment.end_frame, segment.id))
        check_translation_cancelled(check_cancelled)
        return PreparedSegmentTranslation(
            document_id=target_document_id,
            baseline=tuple(existing),
            segments=tuple(updated),
            result=tuple([*replacements.values(), *additions]),
            source_document_id=source_document_id,
            source_segments=tuple(selected),
        )

    def translate_selected_to_document(
        self,
        source_document_id: str,
        target_document_id: str,
        segment_ids: list[str],
        *,
        target_language: str,
        provider: LlmProviderSettings,
        mode: TranslationMode = "standard",
        glossary: list[GlossaryTermSettings] | None = None,
        progress: TranslationProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> list[SubtitleSegment]:
        return self.commit_segment_translation(
            self.prepare_selected_to_document_translation(
                source_document_id,
                target_document_id,
                segment_ids,
                target_language=target_language,
                provider=provider,
                mode=mode,
                glossary=glossary,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        )

    def commit_segment_translation(
        self,
        prepared: PreparedSegmentTranslation,
    ) -> list[SubtitleSegment]:
        def merge_translation() -> None:
            source = {
                item.id: item
                for item in self.repository.subtitles.list_subtitle_segments(prepared.source_document_id)
            }
            if any(source.get(item.id) != item for item in prepared.source_segments):
                raise RuntimeError("翻译期间源字幕已修改，未覆盖当前内容，请重新翻译")
            before = {item.id: item for item in prepared.baseline}
            after = {item.id: item for item in prepared.segments}
            changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
            current = {
                item.id: item
                for item in self.repository.subtitles.list_subtitle_segments(prepared.document_id)
            }
            if any(current.get(key) != before.get(key) for key in changed):
                raise RuntimeError("翻译期间目标字幕已修改，未覆盖你的修改，请重新翻译")
            # A concurrent translation can add a different ID for the same source.
            additions = {item.source_segment_id for item in prepared.result if item.id not in before}
            if any(
                item.id not in before and item.source_segment_id in additions for item in current.values()
            ):
                raise RuntimeError("翻译期间目标字幕已新增对应译文，请刷新后重试")
            for key in changed:
                if key in after:
                    current[key] = after[key]
                else:
                    current.pop(key, None)
            merged = sorted(current.values(), key=lambda item: (item.start_frame, item.end_frame, item.id))
            self.repository.subtitles.save_subtitle_segments(prepared.document_id, merged)

        self.publication.commit_document_change(prepared.document_id, merge_translation)
        return list(prepared.result)
