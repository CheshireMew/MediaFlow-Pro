from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.task_commands import DiagnosticsBundleCommand


def create_bundle(context: OperationContext) -> dict:
    command = DiagnosticsBundleCommand(
        output_path=str(context.required("output_path")),
        task_ids=[str(value) for value in context.arguments.get("task_ids", [])],
        overwrite=bool(context.arguments.get("overwrite", False)),
    )
    command.validate_for_execution()
    return context.task_receipt(
        context.project.start_task(
            command,
            idempotency_key=context.task_idempotency(),
        )
    )
