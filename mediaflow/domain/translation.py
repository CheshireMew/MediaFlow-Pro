from __future__ import annotations

from typing import Literal, cast

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


def validate_translation_mode(value: str) -> TranslationMode:
    if value not in TRANSLATION_MODES:
        raise ValueError(f"Unsupported translation mode: {value}")
    return cast(TranslationMode, value)


def validate_translation_language(value: str) -> str:
    if value not in TRANSLATION_LANGUAGES:
        raise ValueError(f"Unsupported translation language: {value}")
    return value
