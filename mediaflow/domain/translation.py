from __future__ import annotations

from typing import Literal, cast

from mediaflow.domain.model_base import DomainModel

TranslationMode = Literal["standard", "intelligent", "proofread"]

TRANSLATION_MODES: tuple[TranslationMode, ...] = (
    "standard",
    "intelligent",
    "proofread",
)

TRANSLATION_LANGUAGES = (
    "zh_CN",
    "en",
    "ja",
    "zh_TW",
    "ko",
    "es",
    "fr",
    "de",
    "ru",
)


class TranslationComparisonRow(DomainModel):
    row_id: str
    source_segment_ids: list[str]
    source_text: str
    target_segment_id: str = ""
    target_text: str = ""
    start_frame: int
    end_frame: int
    status: Literal["translated", "missing"]


class TranslationComparison(DomainModel):
    source_document_id: str
    target_document_id: str = ""
    source_language: str
    target_language: str
    glossary_hit_count: int = 0
    rows: list[TranslationComparisonRow]


def validate_translation_mode(value: str) -> TranslationMode:
    if value not in TRANSLATION_MODES:
        raise ValueError(f"Unsupported translation mode: {value}")
    return cast(TranslationMode, value)


def validate_translation_language(value: str) -> str:
    if value not in TRANSLATION_LANGUAGES:
        raise ValueError(f"Unsupported translation language: {value}")
    return value
