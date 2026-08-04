from __future__ import annotations

from mediaflow.automation.operation_context import (
    OperationContext,
    project_snapshot,
)


def create_project(context: OperationContext) -> dict:
    return project_snapshot(context.project)


def inspect_project(context: OperationContext) -> dict:
    return project_snapshot(context.project)


def upgrade_project(context: OperationContext) -> dict:
    if not context.project.has_pending_project_upgrade():
        raise ValueError("The project already uses the current schema")
    return {
        "upgraded": True,
        **project_snapshot(context.project),
    }


def list_versions(context: OperationContext) -> dict:
    return {"versions": context.project.list_versions()}


def create_version(context: OperationContext) -> dict:
    record = context.project.create_version(str(context.required("name")))
    return {"version": record}


def restore_version(context: OperationContext) -> dict:
    record = context.project.restore_version(
        str(context.required("version_id"))
    )
    return {
        "restored_version": record,
        **project_snapshot(context.project),
    }


def list_assets(context: OperationContext) -> dict:
    return {"assets": context.project.list_assets()}


def import_asset(context: OperationContext) -> dict:
    task = context.project.import_asset(
        str(context.required("source")),
        idempotency_key=context.task_idempotency(),
    )
    return context.task_receipt(task)


def create_short_sequence(context: OperationContext) -> dict:
    sequence = context.project.create_short_from_bounds(
        str(context.required("source_sequence_id")),
        int(context.required("start_frame")),
        int(context.required("end_frame")),
        name=str(context.arguments.get("name") or "短视频"),
    )
    return {"sequence": sequence}
