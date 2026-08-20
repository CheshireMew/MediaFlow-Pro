from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mediaflow.domain.translation import TRANSLATION_LANGUAGES, TRANSLATION_MODES


def translation_mode_options() -> list[dict[str, str]]:
    labels = {
        "standard": QCoreApplication.translate("TranslationCatalog", "标准翻译"),
        "intelligent": QCoreApplication.translate("TranslationCatalog", "智能翻译"),
        "proofread": QCoreApplication.translate("TranslationCatalog", "原文校对"),
    }
    return [
        {
            "label": labels[value],
            "value": value,
        }
        for value in TRANSLATION_MODES
    ]


def translation_language_options() -> list[dict[str, str]]:
    labels = {
        "zh_CN": QCoreApplication.translate("TranslationCatalog", "简体中文"),
        "en": QCoreApplication.translate("TranslationCatalog", "英语"),
        "ja": QCoreApplication.translate("TranslationCatalog", "日语"),
        "zh_TW": QCoreApplication.translate("TranslationCatalog", "繁体中文"),
        "ko": QCoreApplication.translate("TranslationCatalog", "韩语"),
        "es": QCoreApplication.translate("TranslationCatalog", "西班牙语"),
        "fr": QCoreApplication.translate("TranslationCatalog", "法语"),
        "de": QCoreApplication.translate("TranslationCatalog", "德语"),
        "ru": QCoreApplication.translate("TranslationCatalog", "俄语"),
    }
    return [
        {
            "label": labels[value],
            "value": value,
        }
        for value in TRANSLATION_LANGUAGES
    ]
