from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.ports import (
    JsonClient,
    JsonClientFactory,
    TranslationCachePort,
)
from mediaflow.application.translation_execution import check_translation_cancelled
from mediaflow.domain.settings import GlossaryTermSettings, LlmProviderSettings
from mediaflow.domain.subtitles import SubtitleSegment
from mediaflow.domain.translation import TranslationMode


class SubtitleTranslationStrategy:
    """Provider requests, response validation, retries, and translation cache."""

    def __init__(self, client_factory: JsonClientFactory, cache: TranslationCachePort):
        self.client_factory = client_factory
        self.cache = cache

    def translate_strict_batch(
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
        check_translation_cancelled(check_cancelled)
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
            check_translation_cancelled(check_cancelled)
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
            raise RuntimeError(f"Translation failed for subtitle segments: {failed_ids}") from failures[0][1]
        return result

    def translate_intelligent_batch(
        self,
        provider: LlmProviderSettings,
        segments: list[SubtitleSegment],
        *,
        document_id: str,
        target_language: str,
        glossary: list[GlossaryTermSettings],
    ) -> list[SubtitleSegment]:
        response = self.client_factory(provider).complete_json(
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
    def relevant_glossary(
        segments: list[SubtitleSegment],
        glossary: list[GlossaryTermSettings],
    ) -> list[GlossaryTermSettings]:
        source_text = "\n".join(item.text for item in segments).casefold()
        return [term for term in glossary if term.source.casefold() in source_text]
