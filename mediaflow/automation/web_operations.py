from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.task_commands import (
    ExportWebClipCommand,
    RenderWebClipCommand,
)
from mediaflow.domain.web_media import WebExportFormat


def import_web(context: OperationContext) -> dict:
    asset = context.project.import_web_package(str(context.required("source")))
    return {
        "asset": asset.model_dump(mode="json"),
        "web_asset": context.project.inspect_web_asset(asset.id).model_dump(
            mode="json"
        ),
    }


def inspect_web(context: OperationContext) -> dict:
    spec = context.project.inspect_web_asset(
        str(context.required("asset_id"))
    )
    return {"web_asset": spec.model_dump(mode="json")}


def get_web_clip(context: OperationContext) -> dict:
    state = context.project.get_web_clip(str(context.required("clip_id")))
    return {"web_clip_state": state.model_dump(mode="json")}


def update_web_clip(context: OperationContext) -> dict:
    state = context.project.update_web_clip(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        dict(context.required("updates")),
        scene_id=(
            str(context.arguments["scene_id"])
            if context.arguments.get("scene_id")
            else None
        ),
        expected_revision=_expected_revision(context),
        actor=context.actor(),
    )
    return {"web_clip_state": state.model_dump(mode="json")}


def diff_web_clip(context: OperationContext) -> dict:
    diff = context.project.diff_web_clip_update(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        dict(context.required("updates")),
        scene_id=(
            str(context.arguments["scene_id"])
            if context.arguments.get("scene_id")
            else None
        ),
        expected_revision=_expected_revision(context),
        actor=context.actor(),
    )
    return {"diff": diff.model_dump(mode="json")}


def select_variant(context: OperationContext) -> dict:
    state = context.project.select_web_variant(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("variant_id")),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state.model_dump(mode="json")}


def set_keyframe(context: OperationContext) -> dict:
    state = context.project.set_web_keyframe(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("layer_id")),
        str(context.required("field")),
        int(context.required("time_ms")),
        cast(JsonValue, context.arguments["value"]),
        scene_id=str(context.required("scene_id")),
        easing=dict(context.arguments.get("easing") or {}),
        expected_revision=_expected_revision(context),
        actor=context.actor(),
    )
    return {"web_clip_state": state.model_dump(mode="json")}


def remove_keyframe(context: OperationContext) -> dict:
    state = context.project.remove_web_keyframe(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("layer_id")),
        str(context.required("field")),
        int(context.required("time_ms")),
        scene_id=str(context.required("scene_id")),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state.model_dump(mode="json")}


def update_theme(context: OperationContext) -> dict:
    state = context.project.update_web_theme(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        dict(context.required("changes")),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state.model_dump(mode="json")}


def update_data(context: OperationContext) -> dict:
    state = context.project.update_web_data(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        dict(context.required("values")),
        scene_id=str(context.required("scene_id")),
        source_kind=cast(
            Literal["inline", "file", "api"],
            str(context.arguments.get("source_kind", "inline")),
        ),
        source_label=str(context.arguments.get("source_label", "")),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state.model_dump(mode="json")}


def snapshot_data(context: OperationContext) -> dict:
    state = context.project.update_web_data_from_file(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("source")),
        scene_id=str(context.required("scene_id")),
        field_id=(
            str(context.arguments["field_id"])
            if context.arguments.get("field_id")
            else None
        ),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state.model_dump(mode="json")}


def update_locks(context: OperationContext) -> dict:
    state = context.project.set_web_field_locks(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("layer_id")),
        [str(value) for value in context.required("fields")],
        bool(context.required("locked")),
        scene_id=str(context.required("scene_id")),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state.model_dump(mode="json")}


def render_web_clip(context: OperationContext) -> dict:
    command = RenderWebClipCommand(
        sequence_id=str(context.required("sequence_id")),
        clip_id=str(context.required("clip_id")),
    )
    return context.task_result(
        context.project.start_task(
            command,
            sequence_id=command.sequence_id,
            idempotency_key=context.task_idempotency(),
        )
    )


def export_web_clip(context: OperationContext) -> dict:
    command = ExportWebClipCommand(
        sequence_id=str(context.required("sequence_id")),
        clip_id=str(context.required("clip_id")),
        output_path=str(context.required("output_path")),
        format=cast(
            WebExportFormat,
            str(context.required("format")),
        ),
        time_ms=int(context.arguments.get("time_ms", 0)),
        background=str(context.arguments.get("background", "#000000")),
        overwrite=bool(context.arguments.get("overwrite", False)),
    )
    return context.task_result(
        context.project.start_task(
            command,
            sequence_id=command.sequence_id,
            idempotency_key=context.task_idempotency(),
        )
    )


def create_batch(context: OperationContext) -> dict:
    records_value = context.arguments.get("records")
    source_value = context.arguments.get("source")
    if (records_value is None) == (source_value is None):
        raise ValueError(
            "web.batch.create requires exactly one of records or source"
        )
    records: list[Mapping[str, object]]
    if records_value is not None:
        records = [
            dict(value)
            for value in cast(
                list[Mapping[str, object]],
                records_value,
            )
        ]
    else:
        records = context.project.read_web_variant_records(str(source_value))
    variants = context.project.create_web_variants(
        str(context.required("source_sequence_id")),
        str(context.required("clip_id")),
        records,
        {
            str(key): str(value)
            for key, value in dict(context.required("bindings")).items()
        },
        name_template=str(
            context.arguments.get("name_template", "版本 {index}")
        ),
        actor=context.actor(),
    )
    return {
        "variants": [item.model_dump(mode="json") for item in variants]
    }


def rebind_asset(context: OperationContext) -> dict:
    report = context.project.rebind_web_asset(
        str(context.required("asset_id")),
        str(context.required("source")),
        dry_run=bool(context.arguments.get("dry_run", True)),
        allow_conflicts=bool(
            context.arguments.get("allow_conflicts", False)
        ),
    )
    return {"rebind": report.model_dump(mode="json")}


def _expected_revision(context: OperationContext) -> int | None:
    value = context.arguments.get("expected_revision")
    return int(value) if value is not None else None
