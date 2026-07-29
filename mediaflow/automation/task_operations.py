from __future__ import annotations

from pydantic import TypeAdapter

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.task_commands import TaskCommand

_TASK_COMMAND_ADAPTER: TypeAdapter[TaskCommand] = TypeAdapter(TaskCommand)


def list_tasks(context: OperationContext) -> dict:
    return {
        "tasks": [
            item.model_dump(mode="json")
            for item in context.project.list_tasks()
        ]
    }


def get_task(context: OperationContext) -> dict:
    task = context.project.get_task(str(context.required("task_id")))
    return {"task": task.model_dump(mode="json")}


def start_task(context: OperationContext) -> dict:
    task_command = _TASK_COMMAND_ADAPTER.validate_python(
        context.required("task_command")
    )
    task = context.project.start_task(
        task_command,
        [
            str(value)
            for value in context.arguments.get("input_asset_ids") or []
        ],
        sequence_id=context.sequence_id(),
        idempotency_key=context.task_idempotency(),
    )
    return context.task_result(task)


def resume_task(context: OperationContext) -> dict:
    return context.task_result(
        context.project.resume_task(
            str(context.required("task_id")),
            allow_existing=context.retrying,
        )
    )
