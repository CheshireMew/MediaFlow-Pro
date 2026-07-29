from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.enums import ExportFormat, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.task_commands import ExportSequenceCommand
from mediaflow.domain.timeline import ClipAudio, ClipTransform


def get_timeline(context: OperationContext) -> dict:
    return {
        "timeline": context.project.timeline(context.sequence_id()).state.model_dump(
            mode="json"
        )
    }


def add_track(context: OperationContext) -> dict:
    track = context.project.timeline(context.sequence_id()).add_track(
        TrackKind(str(context.required("kind"))),
        (
            str(context.arguments["name"])
            if context.arguments.get("name")
            else None
        ),
    )
    return {"track": track.model_dump(mode="json")}


def add_clip(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).add_clip(
        track_id=str(context.required("track_id")),
        asset_id=str(context.required("asset_id")),
        timeline_start=int(context.required("timeline_start")),
        source_in=int(context.required("source_in")),
        duration=int(context.required("duration")),
        speed_numerator=int(context.arguments.get("speed_numerator", 1)),
        speed_denominator=int(context.arguments.get("speed_denominator", 1)),
    )
    return {"clip": clip.model_dump(mode="json")}


def move_clip(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).move_clip(
        str(context.required("clip_id")),
        timeline_start=int(context.required("timeline_start")),
        track_id=(
            str(context.arguments["track_id"])
            if context.arguments.get("track_id")
            else None
        ),
    )
    return {"clip": clip.model_dump(mode="json")}


def copy_clip(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).copy_clip(
        str(context.required("clip_id")),
        timeline_start=int(context.required("timeline_start")),
        track_id=(
            str(context.arguments["track_id"])
            if context.arguments.get("track_id")
            else None
        ),
    )
    return {"clip": clip.model_dump(mode="json")}


def split_clip(context: OperationContext) -> dict:
    clips = context.project.timeline(context.sequence_id()).split_clip(
        str(context.required("clip_id")),
        int(context.required("split_frame")),
    )
    return {"clips": [clip.model_dump(mode="json") for clip in clips]}


def delete_clips(context: OperationContext) -> dict:
    editor = context.project.timeline(context.sequence_id())
    editor.delete_clips(
        [str(value) for value in context.required("clip_ids")],
        ripple=bool(context.arguments.get("ripple", False)),
    )
    return {"timeline": editor.state.model_dump(mode="json")}


def transform_clip(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).set_clip_transform(
        str(context.required("clip_id")),
        ClipTransform.model_validate(context.required("transform")),
    )
    return {"clip": clip.model_dump(mode="json")}


def update_clip_audio(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).set_clip_audio(
        str(context.required("clip_id")),
        ClipAudio.model_validate(context.required("audio")),
    )
    return {"clip": clip.model_dump(mode="json")}


def undo(context: OperationContext) -> dict:
    state = context.project.timeline(context.sequence_id()).undo()
    return {"timeline": state.model_dump(mode="json")}


def redo(context: OperationContext) -> dict:
    state = context.project.timeline(context.sequence_id()).redo()
    return {"timeline": state.model_dump(mode="json")}


def render_preview(context: OperationContext) -> dict:
    state = context.project.timeline(context.sequence_id()).state
    context.project.prepare_web_sequence(state)
    path = context.application.write_preview_snapshot(
        context.project.project_dir,
        state,
        use_proxies=bool(context.arguments.get("use_proxies", True)),
        prefer_sdr_preview_proxy=True,
    )
    return {"preview_graph": str(path)}


def export_sequence(context: OperationContext) -> dict:
    preset_value = context.arguments.get("preset")
    sequence_id = context.sequence_id()
    command = ExportSequenceCommand(
        sequence_id=sequence_id,
        output_path=str(context.required("output_path")),
        format=ExportFormat(str(context.arguments.get("format", "h264"))),
        preset=(
            ExportPreset.model_validate(preset_value) if preset_value else None
        ),
        overwrite=bool(context.arguments.get("overwrite", False)),
    )
    return context.task_result(
        context.project.start_task(
            command,
            sequence_id=sequence_id,
            idempotency_key=context.task_idempotency(),
        )
    )
