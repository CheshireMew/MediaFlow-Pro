from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject

from mediaflow.desktop.session_events import SessionEvents


@dataclass(frozen=True, slots=True)
class SessionChangeSet:
    project: bool = False
    selection: bool = False
    history: bool = False
    status: bool = False
    tasks: bool = False
    preview_graph: bool = False
    profile_confirmation: bool = False
    settings: bool = False
    relink_confirmation: bool = False
    audio_metrics: bool = False
    workflow: bool = False
    download_plan: bool = False
    runtime_tools: bool = False
    export_capability: bool = False
    error_reference: bool = False
    error_history: bool = False
    collaboration_conflict: bool = False
    waveform_asset_id: str | None = None


class SessionUpdates(QObject):
    """The sole presentation-state commit path for a desktop session."""

    def __init__(self, events: SessionEvents, parent: QObject) -> None:
        super().__init__(parent)
        self._events = events

    def commit(
        self,
        change: SessionChangeSet | None = None,
        *,
        project: bool = False,
        selection: bool = False,
        history: bool = False,
        status: bool = False,
        tasks: bool = False,
        preview_graph: bool = False,
        profile_confirmation: bool = False,
        settings: bool = False,
        relink_confirmation: bool = False,
        audio_metrics: bool = False,
        workflow: bool = False,
        download_plan: bool = False,
        runtime_tools: bool = False,
        export_capability: bool = False,
        error_reference: bool = False,
        error_history: bool = False,
        collaboration_conflict: bool = False,
        waveform_asset_id: str | None = None,
    ) -> None:
        named = SessionChangeSet(
            project=project,
            selection=selection,
            history=history,
            status=status,
            tasks=tasks,
            preview_graph=preview_graph,
            profile_confirmation=profile_confirmation,
            settings=settings,
            relink_confirmation=relink_confirmation,
            audio_metrics=audio_metrics,
            workflow=workflow,
            download_plan=download_plan,
            runtime_tools=runtime_tools,
            export_capability=export_capability,
            error_reference=error_reference,
            error_history=error_history,
            collaboration_conflict=collaboration_conflict,
            waveform_asset_id=waveform_asset_id,
        )
        if change is not None and named != SessionChangeSet():
            raise TypeError("Pass either a SessionChangeSet or named change fields")
        resolved = change or named
        ordered_signals = (
            (resolved.project, self._events.projectStateChanged),
            (resolved.selection, self._events.selectionChanged),
            (resolved.history, self._events.historyChanged),
            (resolved.status, self._events.statusChanged),
            (resolved.tasks, self._events.tasksChanged),
            (resolved.preview_graph, self._events.previewGraphChanged),
            (resolved.profile_confirmation, self._events.profileConfirmationChanged),
            (resolved.settings, self._events.settingsChanged),
            (resolved.relink_confirmation, self._events.relinkConfirmationChanged),
            (resolved.audio_metrics, self._events.audioMetricsChanged),
            (resolved.workflow, self._events.workflowChanged),
            (resolved.download_plan, self._events.downloadPlanChanged),
            (resolved.runtime_tools, self._events.runtimeToolsChanged),
            (resolved.export_capability, self._events.exportCapabilityChanged),
            (resolved.error_reference, self._events.errorReferenceChanged),
            (resolved.error_history, self._events.errorHistoryChanged),
            (resolved.collaboration_conflict, self._events.collaborationConflictChanged),
        )
        for changed, signal in ordered_signals:
            if changed:
                signal.emit()
        if resolved.waveform_asset_id is not None:
            self._events.waveformDataChanged.emit(resolved.waveform_asset_id)

    def report_error(self, message: str) -> None:
        self._events.errorOccurred.emit(message)

    def request_preview_range(self, start_frame: int, end_frame: int) -> None:
        self._events.previewRangeRequested.emit(start_frame, end_frame)

    def receive_workspace_command(self, event: object) -> None:
        self._events.workspaceCommandReceived.emit(event)
