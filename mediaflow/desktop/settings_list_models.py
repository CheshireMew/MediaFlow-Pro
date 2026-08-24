from __future__ import annotations

from PySide6.QtCore import (
    QObject,
)

from .list_model_base import DictListModel


class GlossaryTermListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(["termId", "source", "target", "note", "category"], parent)


class LlmProviderListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            ["providerId", "name", "baseUrl", "apiKey", "model", "providerEnabled", "active"],
            parent,
        )
