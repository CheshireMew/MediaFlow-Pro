from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mediaflow.automation.contracts import AutomationRequest
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.domain.collaboration import ActorIdentity


class AutomationRequestFactory:
    """Build the canonical public request used by desktop, CLI and MCP clients."""

    def create(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        project_path: str | Path | None,
        content_revision: int | None,
        actor: ActorIdentity,
        client_id: str,
    ) -> AutomationRequest:
        try:
            definition = OPERATIONS[operation]
        except KeyError as error:
            raise ValueError(f"Unknown automation operation: {operation}") from error
        validated = definition.validate_arguments(arguments)
        if definition.project_access == "none":
            resolved_project = None
            base_revision = None
        else:
            if project_path is None:
                raise ValueError(f"{operation} requires an open project")
            resolved_project = str(Path(project_path).resolve())
            if content_revision is None:
                raise ValueError(f"{operation} requires the current project revision")
            base_revision = int(content_revision)
        identity = actor.model_dump(mode="json")
        stable_input = {
            "operation": operation,
            "project": resolved_project,
            "arguments": validated,
            "base_revision": base_revision,
            "actor": identity,
            "client_id": client_id,
        }
        digest = hashlib.sha256(
            json.dumps(
                stable_input,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return AutomationRequest(
            operation=operation,
            project=resolved_project,
            arguments=validated,
            request_id=f"desktop-{digest[:32]}",
            base_revision=base_revision,
            actor=actor,
            client_id=client_id,
        )

    def canonical_json(self, request: AutomationRequest) -> str:
        return json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"
