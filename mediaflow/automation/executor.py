from __future__ import annotations

from collections.abc import Callable

from mediaflow.automation.contracts import AutomationRequest
from mediaflow.automation.operation_context import OperationContext
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.composition import EditorApplication, EditorProject
from mediaflow.domain.collaboration import ProjectChangeEvent

_UNSET_REQUEST_BASE = object()


def execute_operation(
    project: EditorProject,
    application: EditorApplication,
    envelope: AutomationRequest,
    *,
    request_base_revision: int | None | object = _UNSET_REQUEST_BASE,
    on_event: Callable[[ProjectChangeEvent], None] | None = None,
) -> tuple[dict, ProjectChangeEvent | None]:
    definition = OPERATIONS[envelope.operation]
    result, event = project.execute_automation_request(
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
        base_revision=(
            0
            if definition.project_access == "create"
            else envelope.base_revision
        ),
        idempotency_base_revision=(
            envelope.base_revision
            if request_base_revision is _UNSET_REQUEST_BASE
            else request_base_revision
        ),
        actor=envelope.actor,
        write_set=definition.write_set(envelope.operation, envelope.arguments),
        undo_group_id=envelope.undo_group_id,
        on_event=on_event,
        force_event=envelope.operation in {"project.create", "project.upgrade"},
        reversible=definition.history_mode == "reversible",
    )
    return definition.validate_result(result), event
