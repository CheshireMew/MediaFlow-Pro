from __future__ import annotations

from mediaflow.automation.operation_context import (
    OperationContext,
    project_snapshot,
)


def create_project(context: OperationContext) -> dict:
    return project_snapshot(context.project)


def inspect_project(context: OperationContext) -> dict:
    return project_snapshot(context.project)


def list_versions(context: OperationContext) -> dict:
    return {
        "versions": [
            item.model_dump(mode="json")
            for item in context.project.list_versions()
        ]
    }


def restore_version(context: OperationContext) -> dict:
    record = context.project.restore_version(
        str(context.required("version_id"))
    )
    return {
        "restored_version": record.model_dump(mode="json"),
        **project_snapshot(context.project),
    }


def list_assets(context: OperationContext) -> dict:
    return {
        "assets": [
            item.model_dump(mode="json")
            for item in context.project.list_assets()
        ]
    }


def import_asset(context: OperationContext) -> dict:
    task = context.project.import_asset(
        str(context.required("source")),
        idempotency_key=context.task_idempotency(),
    )
    result = context.task_result(task)
    completed = result["task"]
    if completed["status"] != "completed":
        raise RuntimeError(completed.get("error") or "Asset import failed")
    imported_id = result["result"]["imported_asset_id"]
    return {
        **result,
        "asset": context.project.get_asset(imported_id).model_dump(
            mode="json"
        ),
    }


def create_short_sequence(context: OperationContext) -> dict:
    sequence = context.project.create_short_from_bounds(
        str(context.required("source_sequence_id")),
        int(context.required("start_frame")),
        int(context.required("end_frame")),
        name=str(context.arguments.get("name") or "短视频"),
    )
    return {"sequence": sequence.model_dump(mode="json")}
