from __future__ import annotations

from PySide6.QtCore import Signal, Slot

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
        command = str(event.get("command") or "")
        arguments = event.get("arguments")
        values = arguments if isinstance(arguments, dict) else {}
        if command == "playhead.seek":
            self.remoteSeekRequested.emit(int(values["frame"]))
        elif command == "playback.play":
            self.remotePlayRequested.emit(int(values["frame"]))
        elif command == "playback.pause":
            self.remotePauseRequested.emit()
        elif command == "playback.stop":
            self.remoteStopRequested.emit()
        elif command == "workspace.mode.activate":
            self.remoteModeRequested.emit(str(values["mode"]))
        elif command == "timeline.selection.set":
            apply_remote_timeline_selection(self._session, values)
