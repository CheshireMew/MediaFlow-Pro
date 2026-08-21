from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.enums import (
    ExportFormat,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.exports import ExportPreset, SubtitleStyle
from mediaflow.domain.task_commands import (
    BuildSequenceCommand,
    ExportSequenceCommand,
    SequenceBuildUnit,
)
from mediaflow.domain.timeline import (
    ClipAddRequest,
    ClipAudio,
    ClipTransform,
    FreezeClipAddRequest,
)
from mediaflow.file_digest import sha256_file


def get_timeline(context: OperationContext) -> dict:
    return {"timeline": context.project.timeline(context.sequence_id()).state}


def inspect_portable_timeline(context: OperationContext) -> dict:
    loaded = context.project.inspect_portable_timeline(str(context.required("timeline_path")))
    document = loaded.document
    return {
        "timeline_path": str(loaded.path),
        "timeline_sha256": loaded.sha256,
        "project_id": document.project_id,
        "profile": document.profile,
        "duration_seconds": document.duration_seconds,
        "source_count": len(document.sources),
        "track_count": len(document.tracks),
        "clip_count": sum(len(track.clips) for track in document.tracks),
        "marker_count": len(document.markers),
        "mediaflow_compatible": True,
    }


def import_portable_timeline(context: OperationContext) -> dict:
    loaded, state, assets, subtitle_document_ids = context.project.import_portable_timeline(
        str(context.required("timeline_path")),
        sequence_id=context.sequence_id(),
    )
    inspected = inspect_portable_timeline(context)
    return {
        **inspected,
        "timeline": state,
        "source_assets": assets,
        "subtitle_document_ids": subtitle_document_ids,
    }


def add_track(context: OperationContext) -> dict:
    track = context.project.timeline(context.sequence_id()).add_track(
        TrackKind(str(context.required("kind"))),
        (str(context.arguments["name"]) if context.arguments.get("name") else None),
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


def add_clips(context: OperationContext) -> dict:
    editor = context.project.timeline(context.sequence_id())
    clips = editor.add_clips([ClipAddRequest.model_validate(value) for value in context.required("clips")])
    return {"clips": clips}


def add_freeze_clip(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).add_freeze_clip(
        FreezeClipAddRequest(
            track_id=str(context.required("track_id")),
            asset_id=str(context.required("asset_id")),
            timeline_start=int(context.required("timeline_start")),
            source_frame=int(context.required("source_frame")),
            duration=int(context.required("duration")),
        )
    )
    return {"clip": clip}


def move_clip(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).move_clip(
        str(context.required("clip_id")),
        timeline_start=int(context.required("timeline_start")),
        track_id=(str(context.arguments["track_id"]) if context.arguments.get("track_id") else None),
    )
    return {"clip": clip}


def copy_clip(context: OperationContext) -> dict:
    clip = context.project.timeline(context.sequence_id()).copy_clip(
        str(context.required("clip_id")),
        timeline_start=int(context.required("timeline_start")),
        track_id=(str(context.arguments["track_id"]) if context.arguments.get("track_id") else None),
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


def add_transition(context: OperationContext) -> dict:
    transition = context.project.timeline(context.sequence_id()).create_transition(
        str(context.required("left_clip_id")),
        str(context.required("right_clip_id")),
        TransitionKind(str(context.required("kind"))),
        int(context.required("duration")),
    )
    return {"transition": transition}


def update_transition(context: OperationContext) -> dict:
    parameters = context.arguments.get("parameters")
    transition = context.project.timeline(context.sequence_id()).update_transition(
        str(context.required("transition_id")),
        kind=TransitionKind(str(context.required("kind"))),
        duration=int(context.required("duration")),
        parameters=dict(parameters) if parameters is not None else None,
    )
    return {"transition": transition}


def remove_transition(context: OperationContext) -> dict:
    context.project.timeline(context.sequence_id()).remove_transition(str(context.required("transition_id")))
    return {"removed": True}


def add_marker(context: OperationContext) -> dict:
    marker = context.project.timeline(context.sequence_id()).add_marker(
        int(context.required("frame")),
        name=str(context.arguments.get("name") or ""),
        color=str(context.arguments.get("color") or "#4ea1ff"),
    )
    return {"marker": marker}


def update_marker(context: OperationContext) -> dict:
    marker = context.project.timeline(context.sequence_id()).update_marker(
        str(context.required("marker_id")),
        frame=int(context.required("frame")),
        name=str(context.arguments.get("name") or ""),
        color=str(context.required("color")),
    )
    return {"marker": marker}


def remove_marker(context: OperationContext) -> dict:
    context.project.timeline(context.sequence_id()).remove_marker(str(context.required("marker_id")))
    return {"removed": True}


def update_subtitle_track_style(context: OperationContext) -> dict:
    track = context.project.timeline(context.sequence_id()).set_subtitle_track_style(
        str(context.required("track_id")),
        SubtitleStyle.model_validate(context.required("style")),
    )
    return {"track": track}


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
        resource_asset_id=(
            str(context.arguments["resource_asset_id"])
            if context.arguments.get("resource_asset_id") is not None
            else None
        ),
    )
    return {"clip": next(item for item in editor.state.clips if item.id == clip_id)}


def update_clip_visual_effect(context: OperationContext) -> dict:
    editor = context.project.timeline(context.sequence_id())
    clip_id = str(context.required("clip_id"))
    editor.update_clip_visual_effect(
        clip_id,
        str(context.required("effect_id")),
        enabled=bool(context.required("enabled")),
        parameters={str(key): float(value) for key, value in dict(context.required("parameters")).items()},
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


def render_preview_frames(context: OperationContext) -> dict:
    state = context.project.timeline(context.sequence_id()).state
    frames = [int(frame) for frame in context.required("frames")]
    if state.duration_frames <= 0:
        raise ValueError("Cannot render proof frames from an empty timeline")
    outside = [frame for frame in frames if frame >= state.duration_frames]
    if outside:
        raise ValueError(
            "Proof frames must be inside the timeline duration; invalid frames: "
            + ", ".join(str(frame) for frame in outside)
        )
    context.project.prepare_web_sequence(state)
    graph, rendered = context.application.render_preview_frames(
        context.project.project_dir,
        state,
        frames,
        use_proxies=bool(context.arguments.get("use_proxies", True)),
        prefer_sdr_preview_proxy=True,
    )
    return {
        "content_revision": context.project.known_content_revision,
        "preview_graph": str(graph),
        "frames": rendered,
    }


def export_sequence(context: OperationContext) -> dict:
    preset_value = context.arguments.get("preset")
    sequence_id = context.sequence_id()
    command = ExportSequenceCommand(
        sequence_id=sequence_id,
        output_path=str(context.required("output_path")),
        format=ExportFormat(str(context.arguments.get("format", "h264"))),
        preset=(ExportPreset.model_validate(preset_value) if preset_value else None),
        overwrite=bool(context.arguments.get("overwrite", False)),
    )
    return context.task_receipt(
        context.project.start_task(
            command,
            sequence_id=sequence_id,
            idempotency_key=context.task_idempotency(),
        )
    )


def build_sequence(context: OperationContext) -> dict:
    preset_value = context.arguments.get("preset")
    sequence_id = context.sequence_id()
    command = BuildSequenceCommand(
        sequence_id=sequence_id,
        units=[SequenceBuildUnit.model_validate(value) for value in context.required("units")],
        output_path=str(context.required("output_path")),
        format=ExportFormat(str(context.arguments.get("format", "h264"))),
        preset=(ExportPreset.model_validate(preset_value) if preset_value else None),
        overwrite=bool(context.arguments.get("overwrite", False)),
    )
    return context.task_receipt(
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
    return {
        "format": "fcpxml",
        "project_id": context.project.get_project().id,
        "sequence_id": sequence_id,
        "timeline_revision": state.sequence.timeline_revision,
        "output_path": str(output),
        "sha256": sha256_file(output),
    }
