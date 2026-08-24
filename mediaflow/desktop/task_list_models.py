from __future__ import annotations

from PySide6.QtCore import (
    QObject,
)

from .list_model_base import DictListModel


class TaskListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "taskId",
                "displayName",
                "configurationLabel",
                "encoderFallbackUsed",
                "commandType",
                "kind",
                "status",
                "statusLabel",
                "progressMode",
                "progressValue",
                "progressCompleted",
                "progressTotal",
                "progressUnit",
                "hasOverallProgress",
                "overallProgressValue",
                "overallProgressCompleted",
                "overallProgressTotal",
                "overallProgressUnit",
                "progressItemIndex",
                "progressItemTotal",
                "progressItemLabel",
                "messageCode",
                "messageLabel",
                "queuePosition",
                "inputAssetIds",
                "contextId",
                "error",
                "artifacts",
                "executionTrace",
                "createdAt",
            ],
            parent,
        )
