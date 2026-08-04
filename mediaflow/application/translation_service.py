from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

from mediaflow.application.ports import (
    JsonClient,
    JsonClientFactory,
    TranslationCachePort,
    TranslationDocuments,
)
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.domain.model_base import new_id
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import GlossaryTermSettings, LlmProviderSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.translation import TranslationMode, validate_translation_mode

TranslationProgress = Callable[[OperationProgress], None]


@dataclass(frozen=True, slots=True)
class _TranslationBatch:
    index: int
    segments: tuple[SubtitleSegment, ...]
    context_before: tuple[SubtitleSegment, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedDocumentTranslation:
    document: SubtitleDocument
    exists: bool
    segments: tuple[SubtitleSegment, ...]


@dataclass(frozen=True, slots=True)
class PreparedSegmentTranslation:
    document_id: str
    segments: tuple[SubtitleSegment, ...]
    result: tuple[SubtitleSegment, ...]


class TranslationService:
    BATCH_SIZE = 10
    CONTEXT_OVERLAP = 3
    MAX_CONCURRENCY = 3

    def __init__(
        self,
        repository: TranslationDocuments,
        client_factory: JsonClientFactory,
        cache: TranslationCachePort,
        publication: SubtitlePublicationService,
    ):
        self.repository = repository
        self.client_factory = client_factory
        self.cache = cache
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

        project = self.repository.catalog.get_project()
        try:
            existing = (
                self.repository.subtitles.get_subtitle_document(operation_id)
                if operation_id
                else None
            )
        except KeyError:
            existing = None
        if existing is not None and (
            existing.is_source
            or existing.source_document_id != source_document.id
            or existing.language != output_language
        ):
            raise RuntimeError("Translation operation id belongs to another result")
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
        batches = self._build_batches(source_segments, mode)

        def translate_batch(batch: _TranslationBatch) -> list[SubtitleSegment]:
            relevant_terms = self._relevant_glossary(list(batch.segments), glossary or [])
            if mode == "intelligent":
                return self._translate_intelligent_batch(
                    self.client_factory(provider),
                    list(batch.segments),
                    document_id=document.id,
                    target_language=output_language,
                    glossary=relevant_terms,
                )
            translated = self._translate_strict_batch(
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

        output_segments = self._run_batches(
            batches,
            translate_batch,
            progress=progress,
            message="proofreading" if mode == "proofread" else "translating",
            check_cancelled=check_cancelled,
        )

        self._checkpoint(check_cancelled)
        if progress:
            progress(OperationProgress.indeterminate("translation_saving"))

        return PreparedDocumentTranslation(
            document=document,
            exists=existing is not None,
            segments=tuple(output_segments),
        )

    def commit_document_translation(
        self,
        prepared: PreparedDocumentTranslation,
    ) -> SubtitleDocument:
        def save_translation() -> None:
            if not prepared.exists:
                self.repository.subtitles.create_subtitle_document(
                    prepared.document,
                    list(prepared.segments),
                )
            else:
                self.repository.subtitles.save_subtitle_document(
                    prepared.document
                )
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
        batches = self._build_batches(segments, mode)

        def translate_batch(batch: _TranslationBatch) -> list[SubtitleSegment]:
            translated = self._translate_strict_batch(
                provider,
                list(batch.segments),
                target_language=language,
                mode=mode,
                glossary=self._relevant_glossary(list(batch.segments), glossary or []),
                context_before=list(batch.context_before),
                check_cancelled=check_cancelled,
            )
            return [
                source.model_copy(update={"text": translated[source.id], "confidence": None})
                for source in batch.segments
            ]

        return self._run_batches(
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
        self._checkpoint(check_cancelled)
        return PreparedSegmentTranslation(
            document_id=document_id,
            segments=tuple(
                replacements.get(segment.id, segment)
                for segment in all_segments
            ),
            result=tuple(translated),
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
        translated = self.translate_segments_preserving_timing(
            selected,
            target_language=target_language,
            provider=provider,
            mode=mode,
            glossary=glossary,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        existing = self.repository.subtitles.list_subtitle_segments(target_document_id)
        selected_ranges = [
            (segment.start_frame, segment.end_frame) for segment in selected
        ]
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
                segment.end_frame > start_frame
                and segment.start_frame < end_frame
                for start_frame, end_frame in selected_ranges
            )

        updated = [
            replacements.get(segment.id, segment)
            for segment in existing
            if keep_existing(segment)
        ] + additions
        updated.sort(key=lambda segment: (segment.start_frame, segment.end_frame, segment.id))
        self._checkpoint(check_cancelled)
        return PreparedSegmentTranslation(
            document_id=target_document_id,
            segments=tuple(updated),
            result=tuple([*replacements.values(), *additions]),
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
        self.publication.commit_document_change(
            prepared.document_id,
            lambda: self.repository.subtitles.save_subtitle_segments(
                prepared.document_id,
                list(prepared.segments),
            ),
        )
        return list(prepared.result)

    def _translate_strict_batch(
        self,
        provider: LlmProviderSettings,
        segments: list[SubtitleSegment],
        *,
        target_language: str,
        mode: TranslationMode,
        glossary: list[GlossaryTermSettings],
        context_before: list[SubtitleSegment],
        check_cancelled: Callable[[], None] | None,
    ) -> dict[str, str]:
        self._checkpoint(check_cancelled)
        request_key = {
            "provider": {
                "base_url": provider.base_url.rstrip("/"),
                "model": provider.model,
            },
            "target_language": target_language,
            "mode": mode,
            "source_texts": [item.text for item in segments],
            "context_before": [item.text for item in context_before],
            "glossary": [
                {
                    "source": term.source,
                    "target": term.target,
                    "note": term.note,
                    "category": term.category,
                }
                for term in glossary
            ],
        }
        cacheable_mode = mode in {"standard", "proofread"}
        if cacheable_mode:
            cached = self.cache.get(request_key)
            if cached is not None and len(cached) == len(segments):
                return {segment.id: text for segment, text in zip(segments, cached, strict=True)}

        client = self.client_factory(provider)
        try:
            result = self._request_strict_translation(
                client,
                segments,
                target_language=target_language,
                mode=mode,
                glossary=glossary,
                context_before=context_before,
            )
        except Exception:
            result = self._translate_single_fallback(
                client,
                segments,
                target_language=target_language,
                mode=mode,
                glossary=glossary,
                check_cancelled=check_cancelled,
            )
            if cacheable_mode:
                self.cache.put(request_key, [result[item.id] for item in segments])
            return result
        if cacheable_mode:
            self.cache.put(request_key, [result[item.id] for item in segments])
        return result

    def _request_strict_translation(
        self,
        client: JsonClient,
        segments: list[SubtitleSegment],
        *,
        target_language: str,
        mode: TranslationMode,
        glossary: list[GlossaryTermSettings],
        context_before: list[SubtitleSegment],
    ) -> dict[str, str]:
        action = "proofread" if mode == "proofread" else "translate"
        language_rule = (
            "Keep the original language; do not translate."
            if mode == "proofread"
            else f"Translate into {target_language}."
        )
        naturalness_rule = (
            "Use natural phrasing, but this is an in-place edit so preserve every segment boundary. "
            if mode == "intelligent"
            else ""
        )
        response = client.complete_json(
            system=(
                f"Professionally {action} subtitle segments. {language_rule} "
                f"{naturalness_rule}"
                "The source may contain ASR errors; correct only what context makes clear. "
                "Preserve every id and exact order. Never merge, split, move meaning between, "
                "or complete one segment with words from another segment. Keep fragments as fragments. "
                "The optional context_before is read-only context and must not appear in the output. "
                'Return only JSON: {"segments":[{"id":"...","text":"..."}]}'
            ),
            payload={
                "mode": mode,
                "target_language": target_language,
                "glossary": [
                    {
                        "source": term.source,
                        "target": term.target,
                        "note": term.note,
                        "category": term.category,
                    }
                    for term in glossary
                ],
                "context_before": [{"id": item.id, "source_text": item.text} for item in context_before],
                "segments": [{"id": item.id, "source_text": item.text} for item in segments],
            },
        )
        translated = response.get("segments")
        if not isinstance(translated, list):
            raise RuntimeError("Translation response is missing segments")
        expected_ids = [item.id for item in segments]
        received_ids = [str(item.get("id")) for item in translated if isinstance(item, dict)]
        if received_ids != expected_ids:
            raise RuntimeError("Translation response changed segment IDs or order")
        result: dict[str, str] = {}
        for item in translated:
            text = str(item.get("text") or "").strip()
            if not text:
                raise RuntimeError(f"Translation is empty for segment {item.get('id')}")
            result[str(item["id"])] = text
        return result

    def _translate_single_fallback(
        self,
        client: JsonClient,
        segments: list[SubtitleSegment],
        *,
        target_language: str,
        mode: TranslationMode,
        glossary: list[GlossaryTermSettings],
        check_cancelled: Callable[[], None] | None,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        failures: list[tuple[str, Exception]] = []
        for segment in segments:
            self._checkpoint(check_cancelled)
            try:
                translated = self._request_strict_translation(
                    client,
                    [segment],
                    target_language=target_language,
                    mode=mode,
                    glossary=glossary,
                    context_before=[],
                )
                result[segment.id] = translated[segment.id]
            except Exception as error:
                failures.append((segment.id, error))
        if failures:
            failed_ids = ", ".join(segment_id for segment_id, _ in failures)
            raise RuntimeError(
                f"Translation failed for subtitle segments: {failed_ids}"
            ) from failures[0][1]
        return result

    def _build_batches(
        self,
        segments: list[SubtitleSegment],
        mode: TranslationMode,
    ) -> list[_TranslationBatch]:
        batches: list[_TranslationBatch] = []
        for index, offset in enumerate(range(0, len(segments), self.BATCH_SIZE)):
            context = (
                segments[max(0, offset - self.CONTEXT_OVERLAP) : offset] if mode != "intelligent" else []
            )
            batches.append(
                _TranslationBatch(
                    index=index,
                    segments=tuple(segments[offset : offset + self.BATCH_SIZE]),
                    context_before=tuple(context),
                )
            )
        return batches

    def _run_batches(
        self,
        batches: list[_TranslationBatch],
        worker: Callable[[_TranslationBatch], list[SubtitleSegment]],
        *,
        progress: TranslationProgress | None,
        message: str,
        check_cancelled: Callable[[], None] | None,
    ) -> list[SubtitleSegment]:
        results: dict[int, list[SubtitleSegment]] = {}
        completed = 0

        def store(batch: _TranslationBatch, value: list[SubtitleSegment]) -> None:
            nonlocal completed
            results[batch.index] = value
            completed += 1
            if progress:
                progress(
                    OperationProgress.determinate(
                        message,
                        completed=completed,
                        total=len(batches),
                        unit="items",
                    )
                )

        if progress:
            progress(
                OperationProgress.determinate(
                    message,
                    completed=0,
                    total=len(batches),
                    unit="items",
                )
            )
        if len(batches) == 1:
            self._checkpoint(check_cancelled)
            store(batches[0], worker(batches[0]))
        else:
            executor = ThreadPoolExecutor(
                max_workers=min(self.MAX_CONCURRENCY, len(batches)),
                thread_name_prefix="mediaflow-translation",
            )
            pending: dict[Future[list[SubtitleSegment]], _TranslationBatch] = {
                executor.submit(worker, batch): batch for batch in batches
            }
            try:
                while pending:
                    self._checkpoint(check_cancelled)
                    done, _ = wait(
                        tuple(pending),
                        timeout=0.05,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        batch = pending.pop(future)
                        store(batch, future.result())
            except Exception:
                for future in pending:
                    future.cancel()
                raise
            finally:
                # A task is not paused, cancelled, or failed until every provider
                # call has actually stopped. Returning while worker threads are
                # still spending quota makes the persisted task state untruthful.
                executor.shutdown(wait=True, cancel_futures=True)
        return [segment for index in range(len(batches)) for segment in results[index]]

    @staticmethod
    def _checkpoint(check_cancelled: Callable[[], None] | None) -> None:
        if check_cancelled:
            check_cancelled()

    def _translate_intelligent_batch(
        self,
        client: JsonClient,
        segments: list[SubtitleSegment],
        *,
        document_id: str,
        target_language: str,
        glossary: list[GlossaryTermSettings],
    ) -> list[SubtitleSegment]:
        response = client.complete_json(
            system=(
                f"Translate subtitles into {target_language} using semantic resegmentation. "
                "You may merge fragments and split long sentences for natural subtitle reading. "
                "Cover all source meaning exactly once and keep chronological order. "
                "For every output segment provide a positive time_percentage representing its share "
                "of the input batch duration. Return only JSON: "
                '{"segments":[{"text":"...","time_percentage":0.5}]}'
            ),
            payload={
                "mode": "intelligent",
                "target_language": target_language,
                "glossary": [
                    {
                        "source": term.source,
                        "target": term.target,
                        "note": term.note,
                        "category": term.category,
                    }
                    for term in glossary
                ],
                "segments": [
                    {
                        "id": item.id,
                        "start_frame": item.start_frame,
                        "end_frame": item.end_frame,
                        "source_text": item.text,
                    }
                    for item in segments
                ],
            },
        )
        values = response.get("segments")
        if not isinstance(values, list) or not values:
            raise RuntimeError("Intelligent translation returned no segments")
        normalized: list[tuple[str, float]] = []
        for item in values:
            if not isinstance(item, dict):
                raise RuntimeError("Intelligent translation segment is not an object")
            text = str(item.get("text") or "").strip()
            raw_percentage = item.get("time_percentage")
            try:
                if raw_percentage is None:
                    raise TypeError
                percentage = float(raw_percentage)
            except (TypeError, ValueError) as error:
                raise RuntimeError("Intelligent translation time percentage is invalid") from error
            if not text or percentage <= 0:
                raise RuntimeError("Intelligent translation requires text and positive time percentages")
            normalized.append((text, percentage))

        total_weight = sum(item[1] for item in normalized)
        batch_start = segments[0].start_frame
        batch_end = segments[-1].end_frame
        duration = batch_end - batch_start
        if len(normalized) > duration:
            raise RuntimeError("Intelligent translation produced more segments than available frames")
        current = batch_start
        output: list[SubtitleSegment] = []
        consumed_weight = 0.0
        for index, (text, weight) in enumerate(normalized):
            consumed_weight += weight
            end = (
                batch_end
                if index == len(normalized) - 1
                else batch_start + round(duration * consumed_weight / total_weight)
            )
            end = max(current + 1, min(batch_end, end))
            output.append(
                SubtitleSegment(
                    document_id=document_id,
                    start_frame=current,
                    end_frame=end,
                    text=text,
                )
            )
            current = end
        return output

    @staticmethod
    def _relevant_glossary(
        segments: list[SubtitleSegment],
        glossary: list[GlossaryTermSettings],
    ) -> list[GlossaryTermSettings]:
        source_text = "\n".join(item.text for item in segments).casefold()
        return [term for term in glossary if term.source.casefold() in source_text]
