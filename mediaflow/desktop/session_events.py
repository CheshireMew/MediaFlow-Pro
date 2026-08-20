from PySide6.QtCore import QObject, Signal

SESSION_EVENT_NAMES = (
    "projectStateChanged",
    "selectionChanged",
    "historyChanged",
    "statusChanged",
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
    "exportCapabilityChanged",
    "previewRangeRequested",
    "errorOccurred",
    "errorReferenceChanged",
    "errorHistoryChanged",
    "collaborationConflictChanged",
)


class SessionEvents(QObject):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    audioMetricsChanged = Signal()
    workflowChanged = Signal()
    downloadPlanChanged = Signal()
    runtimeToolsChanged = Signal()
    waveformDataChanged = Signal(str)
    exportCapabilityChanged = Signal()
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()
    errorHistoryChanged = Signal()
    collaborationConflictChanged = Signal()
    workspaceCommandReceived = Signal(object)
