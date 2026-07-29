from __future__ import annotations

from pathlib import Path
from typing import Any

from mediaflow.automation.contracts import (
    AutomationRequest,
    describe_contract,
    validate_arguments,
)
from mediaflow.automation.operation_context import OperationContext
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.composition import EditorApplication


def _project_path(value: str | None) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("project is required")
    path = Path(text).expanduser()
    return path.parent if path.name == "project.mfp" else path


def _execute_registered(
    project: Any,
    application: EditorApplication,
    envelope: AutomationRequest,
) -> dict[str, Any]:
    definition = OPERATIONS[envelope.operation]
    return project.execute_automation_request(
        (
            envelope.request_id
            if definition.mutates_project
            else None
        ),
        envelope.operation,
        envelope.arguments,
        lambda retrying: definition.handler(
            OperationContext(
                project,
                application,
                envelope,
                retrying=retrying,
            )
        ),
        atomic=definition.execution_mode == "atomic",
    )


def _create_project(
    application: EditorApplication,
    envelope: AutomationRequest,
) -> dict[str, Any]:
    root = _project_path(envelope.project)
    requested_name = str(envelope.arguments["name"])
    if envelope.request_id and (root / "project.mfp").is_file():
        with application.open_project(
            root,
            writable=True,
            cooperative=True,
        ) as project:
            if project.get_project().name != requested_name:
                raise ValueError(
                    "Existing project does not match the retried "
                    "project.create request"
                )
            return _execute_registered(project, application, envelope)
    with application.create_project(root, requested_name) as project:
        return _execute_registered(project, application, envelope)


def execute_request(
    request: dict[str, Any] | AutomationRequest,
    *,
    application: EditorApplication | None = None,
) -> dict[str, Any]:
    envelope = (
        request
        if isinstance(request, AutomationRequest)
        else AutomationRequest.model_validate(request)
    )
    operation = envelope.operation.strip()
    if operation == "describe":
        return describe_contract()
    definition = OPERATIONS.get(operation)
    if definition is None:
        raise ValueError(f"Unknown operation: {operation}")
    envelope = envelope.model_copy(update={"operation": operation})
    validate_arguments(operation, envelope.arguments)
    api = application or EditorApplication()

    if definition.open_mode == "create":
        return _create_project(api, envelope)

    writable = definition.open_mode == "write"
    with api.open_project(
        _project_path(envelope.project),
        writable=writable,
        cooperative=writable,
    ) as project:
        return _execute_registered(project, api, envelope)
