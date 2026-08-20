from __future__ import annotations

from PySide6.QtCore import Signal, Slot

from .controller_facet import ControllerFacet
from .controller_scopes import WorkspacePlaybackScope


class WorkspacePlaybackController(ControllerFacet[WorkspacePlaybackScope]):
    remoteSeekRequested = Signal(int)
    remotePlayRequested = Signal(int)
    remotePauseRequested = Signal()
    remoteStopRequested = Signal()

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
