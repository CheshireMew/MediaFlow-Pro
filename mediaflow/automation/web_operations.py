from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import JsonValue

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.task_commands import (
    ExportWebClipCommand,
    RenderWebClipCommand,
)
from mediaflow.domain.web_media import WebExportFormat, web_asset_spec_document


def import_web(context: OperationContext) -> dict:
    asset = context.project.import_web_package(str(context.required("source")))
    return {
        "asset": asset,
        "web_asset": web_asset_spec_document(
            context.project.inspect_web_asset(asset.id)
        ),
    }


def inspect_web(context: OperationContext) -> dict:
    spec = context.project.inspect_web_asset(
        str(context.required("asset_id"))
    )
    return {"web_asset": web_asset_spec_document(spec)}


def get_web_clip(context: OperationContext) -> dict:
    state = context.project.get_web_clip(str(context.required("clip_id")))
    return {"web_clip_state": state}


def describe_web_clip_editing(context: OperationContext) -> dict:
    document = context.project.describe_web_clip_editing(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        scene_id=(
            str(context.arguments["scene_id"])
            if context.arguments.get("scene_id")
            else None
        ),
    )
    return {"edit_document": document}


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
    return {"web_clip_state": state}


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
    return {"diff": diff}


def select_variant(context: OperationContext) -> dict:
    state = context.project.select_web_variant(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("variant_id")),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state}


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
    return {"web_clip_state": state}


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
    return {"web_clip_state": state}


def update_parameter(context: OperationContext) -> dict:
    state = context.project.update_web_parameter(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("parameter_id")),
        cast(JsonValue, context.arguments["value"]),
        scene_id=(
            str(context.arguments["scene_id"])
            if context.arguments.get("scene_id")
            else None
        ),
        expected_revision=_expected_revision(context),
        actor=context.actor(),
    )
    return {"web_clip_state": state}


def set_parameter_keyframe(context: OperationContext) -> dict:
    state = context.project.set_web_parameter_keyframe(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("parameter_id")),
        int(context.required("time_ms")),
        cast(JsonValue, context.arguments["value"]),
        scene_id=str(context.required("scene_id")),
        easing=dict(context.arguments.get("easing") or {}),
        expected_revision=_expected_revision(context),
        actor=context.actor(),
    )
    return {"web_clip_state": state}


def remove_parameter_keyframe(context: OperationContext) -> dict:
    state = context.project.remove_web_parameter_keyframe(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("parameter_id")),
        int(context.required("time_ms")),
        scene_id=str(context.required("scene_id")),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state}


def update_parameter_lock(context: OperationContext) -> dict:
    state = context.project.set_web_parameter_lock(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        str(context.required("parameter_id")),
        bool(context.required("locked")),
        scene_id=(
            str(context.arguments["scene_id"])
            if context.arguments.get("scene_id")
            else None
        ),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state}


def update_theme(context: OperationContext) -> dict:
    state = context.project.update_web_theme(
        str(context.required("sequence_id")),
        str(context.required("clip_id")),
        dict(context.required("changes")),
        expected_revision=_expected_revision(context),
    )
    return {"web_clip_state": state}


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
    return {"web_clip_state": state}


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
    return {"web_clip_state": state}


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
    return {"web_clip_state": state}


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
        "variants": variants
    }


def plan_rebind_asset(context: OperationContext) -> dict:
    plan = context.project.plan_web_asset_rebind(
        str(context.required("asset_id")),
        str(context.required("source")),
    )
    return {"rebind_plan": plan}


def commit_rebind_asset(context: OperationContext) -> dict:
    report = context.project.commit_web_asset_rebind(
        str(context.required("asset_id")),
        str(context.required("source")),
        str(context.required("plan_digest")),
        {
            str(path): str(resolution)
            for path, resolution in dict(
                context.required("resolutions")
            ).items()
        },
    )
    return {"rebind_commit": report}


def _expected_revision(context: OperationContext) -> int | None:
    value = context.arguments.get("expected_revision")
    return int(value) if value is not None else None
