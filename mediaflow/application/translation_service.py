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
from mediaflow.domain.settings import GlossaryTermSettings, LlmProviderSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.translation import TranslationMode, validate_translation_mode

TranslationProgress = Callable[[float, str], None]


@dataclass(frozen=True, slots=True)
class _TranslationBatch:
    index: int
    segments: tuple[SubtitleSegment, ...]
    context_before: tuple[SubtitleSegment, ...] = ()


class TranslationService:
    BATCH_SIZE = 10
    CONTEXT_OVERLAP = 3
    MAX_CONCURRENCY = 3

    def __init__(
        self,
        repository: TranslationDocuments,
        client_factory: JsonClientFactory,
        cache: TranslationCachePort,
        write_document_srt: Callable[[str], object],
    ):
        self.repository = repository
        self.client_factory = client_factory
        self.cache = cache
        self.write_document_srt = write_document_srt

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
    ) -> SubtitleDocument:
        mode = validate_translation_mode(mode)
        source_document = self.repository.get_subtitle_document(document_id)
        source_segments = self.repository.list_subtitle_segments(document_id)
        if not source_segments:
            raise ValueError("Source subtitle document is empty")
        output_language = source_document.language if mode == "proofread" else target_language.strip()
        if not output_language:
            raise ValueError("Target language is required")

        project = self.repository.get_project()
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=source_document.asset_id,
            media_asset_id=source_document.media_asset_id,
            language=output_language,
            source_document_id=source_document.id,
            is_source=False,
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
            progress_limit=98.0,
            check_cancelled=check_cancelled,
        )

        self.repository.create_subtitle_document(document, output_segments)
        self.write_document_srt(document.id)
        if progress:
            progress(100.0, "translation_completed")
        return document

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
            progress_limit=100.0,
            check_cancelled=check_cancelled,
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
        wanted = set(segment_ids)
        if not wanted:
            raise ValueError("请先选择要翻译的字幕段")
        all_segments = self.repository.list_subtitle_segments(document_id)
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
        self.repository.save_subtitle_segments(
            document_id,
            [replacements.get(segment.id, segment) for segment in all_segments],
        )
        self.write_document_srt(document_id)
        return translated

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
            result, complete = self._translate_single_fallback(
                client,
                segments,
                target_language=target_language,
                mode=mode,
                glossary=glossary,
                check_cancelled=check_cancelled,
            )
            if cacheable_mode and complete:
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
    ) -> tuple[dict[str, str], bool]:
        result: dict[str, str] = {}
        complete = True
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
            except Exception:
                result[segment.id] = segment.text
                complete = False
        return result, complete

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
        progress_limit: float,
        check_cancelled: Callable[[], None] | None,
    ) -> list[SubtitleSegment]:
        results: dict[int, list[SubtitleSegment]] = {}
        completed = 0

        def store(batch: _TranslationBatch, value: list[SubtitleSegment]) -> None:
            nonlocal completed
            results[batch.index] = value
            completed += 1
            if progress:
                progress(completed / len(batches) * progress_limit, message)

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
            failed = False
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
                failed = True
                for future in pending:
                    future.cancel()
                raise
            finally:
                executor.shutdown(wait=not failed, cancel_futures=failed)
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
