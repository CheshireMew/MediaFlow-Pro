from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.project_controller import ProjectSession


CONTROLLER_SIGNALS = (
    "projectStateChanged",
    "selectionChanged",
    "historyChanged",
    "statusChanged",
    "taskDrawerChanged",
    "tasksChanged",
    "previewGraphChanged",
    "profileConfirmationChanged",
    "settingsChanged",
    "relinkConfirmationChanged",
    "audioMetricsChanged",
    "workflowChanged",
    "downloadPlanChanged",
    "runtimeToolsChanged",
    "waveformDataChanged",
    "previewRangeRequested",
    "errorOccurred",
    "errorReferenceChanged",
)


class ControllerFacet(QObject):
    def __init__(self, session: ProjectSession):
        super().__init__(session)
        QObject.__setattr__(self, "_session", session)

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "_session" or not name.startswith("_"):
            QObject.__setattr__(self, name, value)
            return
        setattr(self._session, name, value)
