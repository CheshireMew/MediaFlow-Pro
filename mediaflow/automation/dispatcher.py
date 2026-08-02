from __future__ import annotations

from pathlib import Path
from typing import Any

from mediaflow.automation.contracts import (
    AutomationRequest,
    describe_contract,
)
from mediaflow.automation.operation_context import OperationContext
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.composition import EditorApplication
from mediaflow.domain.storage_names import (
    PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    PROJECT_ROOT_PATH_UTF16_LIMIT,
    safe_child_path,
)
from mediaflow.infrastructure.storage_paths import default_project_root


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
    result = project.execute_automation_request(
        (
            envelope.request_id
            if definition.project_access in {"create", "write"}
            else None
        ),
        envelope.operation,
        envelope.arguments,
        lambda retrying: definition.validate_result(
            definition.handler(
                OperationContext(
                    project,
                    application,
                    envelope,
                    retrying=retrying,
                )
            )
        ),
        atomic=definition.execution_mode == "atomic",
    )
    return definition.validate_result(result)


def _create_project(
    application: EditorApplication,
    envelope: AutomationRequest,
) -> dict[str, Any]:
    if str(envelope.project or "").strip():
        raise ValueError(
            "project.create does not accept project; "
            "MediaFlow Pro owns the default project root"
        )
    root = safe_child_path(
        default_project_root(),
        str(envelope.arguments["directory_name"]),
        max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT,
        max_component_utf16_units=PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    )
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
    envelope = envelope.model_copy(
        update={
            "operation": operation,
            "arguments": definition.validate_arguments(envelope.arguments),
        }
    )

    if definition.project_access == "none":
        return definition.validate_result(
            definition.handler(
                OperationContext(None, application, envelope)
            )
        )

    api = application or EditorApplication()

    if definition.project_access == "create":
        return _create_project(api, envelope)

    writable = definition.project_access == "write"
    with api.open_project(
        _project_path(envelope.project),
        writable=writable,
        cooperative=writable,
    ) as project:
        return _execute_registered(project, api, envelope)
