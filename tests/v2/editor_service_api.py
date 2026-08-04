from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.service.client import (
    call_sync,
    execute_sync,
    shutdown_sync_service,
)


class EditorServiceApi:
    """Sequential protocol-v3 client used by end-to-end service tests.

    The helper remembers only revisions that the caller has actually observed.
    Tests that exercise stale writes pass ``base_revision`` explicitly.
    """

    def __init__(self, *, actor_id: str = "pytest-agent") -> None:
        self.actor = {
            "kind": "agent",
            "id": actor_id,
            "name": "MediaFlow test agent",
        }
        self.client_id = f"pytest-{uuid.uuid4().hex}"
        self._project_revisions: dict[str, int] = {}

    def request(
        self,
        operation: str,
        *,
        project: Path | str | None = None,
        arguments: dict[str, Any] | None = None,
        request_id: str | None = None,
        base_revision: int | None = None,
    ) -> dict[str, Any]:
        definition = OPERATIONS.get(operation)
        access = definition.project_access if definition is not None else "none"
        project_text = str(Path(project).resolve()) if project is not None else None
        if access == "write" and base_revision is None:
            if project_text is None:
                raise ValueError(f"{operation} requires a project")
            base_revision = self.revision(project_text)
        if access in {"create", "write"} and request_id is None:
            request_id = f"{operation}-{uuid.uuid4().hex}"
        value: dict[str, Any] = {
            "protocol": "mediaflow-editor",
            "version": 3,
            "operation": operation,
            "arguments": arguments or {},
            "actor": self.actor,
            "client_id": self.client_id,
        }
        if project_text is not None:
            value["project"] = project_text
        if request_id is not None:
            value["request_id"] = request_id
        if base_revision is not None:
            value["base_revision"] = base_revision
        return value

    def execute(
        self,
        operation: str,
        *,
        project: Path | str | None = None,
        arguments: dict[str, Any] | None = None,
        request_id: str | None = None,
        base_revision: int | None = None,
    ) -> dict[str, Any]:
        return self.execute_request(
            self.request(
                operation,
                project=project,
                arguments=arguments,
                request_id=request_id,
                base_revision=base_revision,
            )
        )["result"]

    def execute_request(self, request: dict[str, Any]) -> dict[str, Any]:
        response = execute_sync(request)
        revision = response.get("project_revision")
        project = request.get("project")
        result = response.get("result")
        if project is None and request.get("operation") == "project.create":
            if isinstance(result, dict):
                project = result.get("path")
        if project is not None and revision is not None:
            self._project_revisions[str(Path(project).resolve())] = int(revision)
        return response

    def revision(self, project: Path | str) -> int:
        project_text = str(Path(project).resolve())
        known = self._project_revisions.get(project_text)
        if known is not None:
            return known
        response = execute_sync(
            {
                "protocol": "mediaflow-editor",
                "version": 3,
                "operation": "project.inspect",
                "project": project_text,
                "arguments": {},
                "actor": self.actor,
                "client_id": self.client_id,
            }
        )
        revision_value = response.get("project_revision")
        if revision_value is None:
            raise RuntimeError("Editor Service returned no project revision")
        revision = int(revision_value)
        self._project_revisions[project_text] = revision
        return revision

    def history(
        self,
        direction: str,
        project: Path | str,
        *,
        request_id: str | None = None,
        undo_group_id: str | None = None,
    ) -> dict[str, Any]:
        if direction not in {"undo", "redo"}:
            raise ValueError(f"Unsupported history direction: {direction}")
        project_text = str(Path(project).resolve())
        params: dict[str, Any] = {
            "project": project_text,
            "request_id": request_id or f"history-{direction}-{uuid.uuid4().hex}",
            "base_revision": self.revision(project_text),
            "actor": self.actor,
        }
        if undo_group_id is not None:
            params["undo_group_id"] = undo_group_id
        response = call_sync(f"history.{direction}", params)
        if not isinstance(response, dict):
            raise RuntimeError("Editor Service history result must be an object")
        revision = response.get("project_revision")
        if revision is not None:
            self._project_revisions[project_text] = int(revision)
        return response

    @staticmethod
    def shutdown() -> None:
        shutdown_sync_service()
