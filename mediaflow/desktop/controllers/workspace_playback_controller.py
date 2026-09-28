from __future__ import annotations

from typing import cast

from PySide6.QtCore import Signal, Slot

from mediaflow.domain.workspace_commands import WorkspaceCommandEvent

from .controller_facet import ControllerFacet
from .controller_scopes import WorkspacePlaybackScope
from .remote_workspace_selection import apply_remote_timeline_selection


class WorkspacePlaybackController(ControllerFacet[WorkspacePlaybackScope]):
    remoteSeekRequested = Signal(int)
    remotePlayRequested = Signal(int)
    remotePauseRequested = Signal()
    remoteStopRequested = Signal()
    remoteModeRequested = Signal(str)

    def __init__(self, session: WorkspacePlaybackScope):
        super().__init__(session)
        session.events.workspaceCommandReceived.connect(self._apply_workspace_command)

    @Slot(object)
    def _apply_workspace_command(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        try:
            accepted = WorkspaceCommandEvent.model_validate(event)
        except ValueError:
            self._session.updates.report_error("远程工作区命令格式无效")
            return
        command = accepted.command
        values = accepted.arguments
        if command == "playhead.seek":
            self.remoteSeekRequested.emit(cast(int, values["frame"]))
        elif command == "playback.play":
            self.remotePlayRequested.emit(cast(int, values["frame"]))
        elif command == "playback.pause":
            self.remotePauseRequested.emit()
        elif command == "playback.stop":
            self.remoteStopRequested.emit()
        elif command == "workspace.mode.activate":
            self.remoteModeRequested.emit(str(values["mode"]))
        elif command == "timeline.selection.set":
            apply_remote_timeline_selection(self._session, values)
