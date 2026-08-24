from __future__ import annotations

from PySide6.QtCore import (
    QObject,
)

from .list_model_base import DictListModel


class SubtitleDocumentListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "documentId",
                "assetId",
                "mediaAssetId",
                "sequenceId",
                "language",
                "isSource",
                "sourceDocumentId",
                "segmentCount",
            ],
            parent,
        )


class SubtitleSegmentListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "segmentId",
                "startFrame",
                "endFrame",
                "text",
                "speaker",
                "confidence",
                "hasOverlap",
            ],
            parent,
        )


class SubtitlePlacementListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "placementId",
                "trackId",
                "documentId",
                "segmentId",
                "clipId",
                "audioTrackPosition",
                "startFrame",
                "endFrame",
                "text",
                "sourceText",
                "hasOverride",
                "timingOverridden",
            ],
            parent,
        )
