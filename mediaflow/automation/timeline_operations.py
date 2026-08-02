from __future__ import annotations

import hashlib

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.enums import ExportFormat, TrackKind, VisualEffectKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.task_commands import ExportSequenceCommand
from mediaflow.domain.timeline import ClipAudio, ClipTransform


def get_timeline(context: OperationContext) -> dict:
    return {"timeline": context.project.timeline(context.sequence_id()).state}


def add_track(context: OperationContext) -> dict:
    track = context.project.timeline(context.sequence_id()).add_track(
        TrackKind(str(context.required("kind"))),
        (
            str(context.arguments["name"])
            if context.arguments.get("name")
            else None
        ),
    )
    return {"track": track}


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
    return {"clip": clip}


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
    return {"clip": clip}


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
    return {"clip": clip}


def split_clip(context: OperationContext) -> dict:
    clips = context.project.timeline(context.sequence_id()).split_clip(
        str(context.required("clip_id")),
        int(context.required("split_frame")),
    )
    return {"clips": list(clips)}


def delete_clips(context: OperationContext) -> dict:
    editor = context.project.timeline(context.sequence_id())
    editor.delete_clips(
        [str(value) for value in context.required("clip_ids")],
        ripple=bool(context.arguments.get("ripple", False)),
    )
    return {"timeline": editor.state}


def transform_clip(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).set_clip_transform(
        str(context.required("clip_id")),
        ClipTransform.model_validate(context.required("transform")),
    )
    return {"clip": clip}


def update_clip_audio(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).set_clip_audio(
        str(context.required("clip_id")),
        ClipAudio.model_validate(context.required("audio")),
    )
    return {"clip": clip}


def replace_clip_source(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).replace_clip_source(
        str(context.required("clip_id")),
        str(context.required("asset_id")),
    )
    return {"clip": clip}


def add_clip_visual_effect(context: OperationContext) -> dict:
    editor = context.project.timeline(context.sequence_id())
    clip_id = str(context.required("clip_id"))
    editor.add_clip_visual_effect(
        clip_id,
        VisualEffectKind(str(context.required("kind"))),
    )
    return {"clip": next(item for item in editor.state.clips if item.id == clip_id)}


def update_clip_visual_effect(context: OperationContext) -> dict:
    editor = context.project.timeline(context.sequence_id())
    clip_id = str(context.required("clip_id"))
    editor.update_clip_visual_effect(
        clip_id,
        str(context.required("effect_id")),
        enabled=bool(context.required("enabled")),
        parameters={
            str(key): float(value)
            for key, value in dict(context.required("parameters")).items()
        },
    )
    return {"clip": next(item for item in editor.state.clips if item.id == clip_id)}


def move_clip_visual_effect(context: OperationContext) -> dict:
    editor = context.project.timeline(context.sequence_id())
    clip_id = str(context.required("clip_id"))
    editor.move_clip_visual_effect(
        clip_id,
        str(context.required("effect_id")),
        int(context.required("position")),
    )
    return {"clip": next(item for item in editor.state.clips if item.id == clip_id)}


def remove_clip_visual_effect(context: OperationContext) -> dict:
    editor = context.project.timeline(context.sequence_id())
    clip_id = str(context.required("clip_id"))
    editor.remove_clip_visual_effect(
        clip_id,
        str(context.required("effect_id")),
    )
    return {"clip": next(item for item in editor.state.clips if item.id == clip_id)}


def undo(context: OperationContext) -> dict:
    state = context.project.timeline(context.sequence_id()).undo()
    return {"timeline": state}


def redo(context: OperationContext) -> dict:
    state = context.project.timeline(context.sequence_id()).redo()
    return {"timeline": state}


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


def export_fcpxml(context: OperationContext) -> dict:
    sequence_id = context.sequence_id()
    state = context.project.timeline(sequence_id).state
    output = context.project.export_fcpxml(
        sequence_id,
        str(context.required("output_path")),
        overwrite=bool(context.arguments.get("overwrite", False)),
    )
    digest = hashlib.sha256()
    with output.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return {
        "format": "fcpxml",
        "project_id": context.project.get_project().id,
        "sequence_id": sequence_id,
        "timeline_revision": state.sequence.timeline_revision,
        "output_path": str(output),
        "sha256": digest.hexdigest(),
    }
