from __future__ import annotations

from PySide6.QtCore import (
    QObject,
)

from .list_model_base import DictListModel


class HighlightListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "highlightId",
                "assetId",
                "documentId",
                "sequenceId",
                "sourceSequenceId",
                "startFrame",
                "endFrame",
                "title",
                "reason",
                "score",
                "selected",
            ],
            parent,
        )


class AudioBusListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "busId",
                "name",
                "displayName",
                "parentBusId",
                "gainDb",
                "muted",
                "solo",
                "channelLayout",
            ],
            parent,
        )


class AudioEffectListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "effectId",
                "busId",
                "kind",
                "displayName",
                "position",
                "enabled",
                "parameters",
            ],
            parent,
        )


class AudioEffectParameterListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            ["key", "descriptor", "value", "options"],
            parent,
        )
