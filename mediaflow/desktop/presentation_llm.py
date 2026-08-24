from __future__ import annotations

from PySide6.QtCore import QCoreApplication


def llm_provider_label(source: str) -> str:
    return source or QCoreApplication.translate(
        "LlmProviderCatalog",
        "自定义 / 本地",
    )


def llm_reasoning_label(model: str) -> str:
    if not model:
        return ""
    return QCoreApplication.translate(
        "LlmProviderCatalog",
        "推理模式（%1）",
    ).replace("%1", model)
