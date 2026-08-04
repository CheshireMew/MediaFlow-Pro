from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mediaflow.domain.model_base import now_ms

WorkspaceCommand = Literal[
    "playhead.seek",
    "playback.play",
    "playback.pause",
    "playback.stop",
]


@dataclass(slots=True)
class WorkspaceSession:
    id: str
    client_id: str
    project: str | None
    attached_at: int
    revision: int = 0
    connections: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "workspace_session_id": self.id,
            "client_id": self.client_id,
            "project": self.project,
            "attached_at": self.attached_at,
            "workspace_revision": self.revision,
            "connected": self.connections > 0,
        }


class WorkspaceRegistry:
    """Service-owned registry for explicitly connected desktop workspaces."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, WorkspaceSession] = {}

    def attach(
        self,
        *,
        client_id: str,
        project: str | None = None,
        workspace_session_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_client = client_id.strip()
        if not normalized_client:
            raise ValueError("client_id is required")
        normalized_project = (
            str(Path(project).expanduser().resolve()) if project else None
        )
        with self._lock:
            if workspace_session_id:
                session = self._require_owned(workspace_session_id, normalized_client)
                session.project = normalized_project
            else:
                session = WorkspaceSession(
                    id=f"workspace-{uuid.uuid4().hex}",
                    client_id=normalized_client,
                    project=normalized_project,
                    attached_at=now_ms(),
                )
                self._sessions[session.id] = session
            return session.snapshot()

    def connect(self, workspace_session_id: str, client_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._require_owned(workspace_session_id, client_id)
            session.connections += 1
            return session.snapshot()

    def disconnect(self, workspace_session_id: str, client_id: str) -> None:
        with self._lock:
            session = self._sessions.get(workspace_session_id)
            if session is None or session.client_id != client_id:
                return
            session.connections = max(0, session.connections - 1)

    def detach(self, workspace_session_id: str, client_id: str) -> None:
        with self._lock:
            session = self._require_owned(workspace_session_id, client_id)
            if session.connections:
                raise RuntimeError("Workspace still has an active event connection")
            self._sessions.pop(session.id)

    def command(
        self,
        workspace_session_id: str,
        command: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            "playhead.seek",
            "playback.play",
            "playback.pause",
            "playback.stop",
        }
        if command not in allowed:
            raise ValueError(f"Unknown workspace command: {command}")
        normalized_arguments = dict(arguments)
        if command in {"playhead.seek", "playback.play"}:
            frame = normalized_arguments.get("frame")
            if type(frame) is not int or frame < 0:
                raise ValueError(f"{command} requires a non-negative integer frame")
            normalized_arguments = {"frame": frame}
        elif normalized_arguments:
            raise ValueError(f"{command} does not accept arguments")
        with self._lock:
            session = self._sessions.get(workspace_session_id)
            if session is None or not session.connections:
                raise RuntimeError(
                    f"Workspace session is not connected: {workspace_session_id}"
                )
            session.revision += 1
            return {
                "workspace_session_id": session.id,
                "workspace_revision": session.revision,
                "project": session.project,
                "command": command,
                "arguments": normalized_arguments,
            }

    def status(self, workspace_session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(workspace_session_id)
            if session is None:
                raise RuntimeError(
                    f"Workspace session is unavailable: {workspace_session_id}"
                )
            return session.snapshot()

    def close(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _require_owned(self, workspace_session_id: str, client_id: str) -> WorkspaceSession:
        normalized_id = workspace_session_id.strip()
        session = self._sessions.get(normalized_id)
        if session is None:
            raise RuntimeError(f"Workspace session is unavailable: {normalized_id}")
        if session.client_id != client_id.strip():
            raise PermissionError("Workspace session belongs to another desktop client")
        return session
