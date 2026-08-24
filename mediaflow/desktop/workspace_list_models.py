from __future__ import annotations

from PySide6.QtCore import (
    QObject,
)

from .list_model_base import DictListModel


class SequenceListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            ["sequenceId", "name", "displayName", "kind", "profile", "colorMode"],
            parent,
        )


class RecentProjectListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "name",
                "path",
                "available",
                "unavailableReason",
                "runningTaskCount",
                "failedTaskCount",
                "offlineAssetCount",
                "pendingWorkflowCount",
                "recentArtifact",
                "coverUrl",
            ],
            parent,
        )


class DownloadEntryListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "entryIndex",
                "mediaId",
                "title",
                "pageUrl",
                "duration",
                "uploader",
                "available",
                "unavailableReason",
                "selected",
            ],
            parent,
        )
