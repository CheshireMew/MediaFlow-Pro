from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediaflow.domain.model_base import now_ms
from mediaflow.domain.workspace_commands import WorkspaceCommandEvent, validate_workspace_arguments


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
        normalized_arguments = validate_workspace_arguments(command, arguments)
        with self._lock:
            session = self._sessions.get(workspace_session_id)
            if session is None or not session.connections:
                raise RuntimeError(
                    f"Workspace session is not connected: {workspace_session_id}"
                )
            event = WorkspaceCommandEvent.model_validate({
                "workspace_session_id": session.id,
                "workspace_revision": session.revision + 1,
                "project": session.project,
                "command": command,
                "arguments": normalized_arguments,
            })
            session.revision = event.workspace_revision
            return event.model_dump(mode="json")

    def status(self, workspace_session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(workspace_session_id)
            if session is None:
                raise RuntimeError(
                    f"Workspace session is unavailable: {workspace_session_id}"
                )
            return session.snapshot()

    def list(
        self,
        *,
        project: str | None = None,
        connected_only: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_project = (
            str(Path(project).expanduser().resolve()) if project else None
        )
        with self._lock:
            sessions = [
                session
                for session in self._sessions.values()
                if (not connected_only or session.connections > 0)
                and (normalized_project is None or session.project == normalized_project)
            ]
            sessions.sort(key=lambda session: (session.attached_at, session.id))
            return [session.snapshot() for session in sessions]

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
