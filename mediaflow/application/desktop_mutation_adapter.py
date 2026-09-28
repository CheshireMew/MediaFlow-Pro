from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from mediaflow.domain.collaboration import ProjectMutationPlan
from mediaflow.domain.model_base import DomainModel

from .project_mutation_planning import (
    ProjectMutationDocuments,
    plan_automation_project_mutation,
)

DesktopMutationTarget = Literal["project", "timeline"]

_OPERATION_ALIASES: dict[tuple[DesktopMutationTarget, str], str] = {
    ("project", "apply_transcript_edit"): "transcript.edit.apply",
    ("project", "close_script_gap"): "script.gap.close",
    ("project", "commit_web_asset_rebind"): "web.asset.rebind.commit",
    ("project", "create_short_sequence"): "sequence.short.create",
    ("project", "collect_project_assets"): "project.collection.apply",
    ("project", "generate_sequence_variants"): "sequence.variant.generate",
    ("project", "set_project_collection_state"): "project.collection.state.set",
    ("project", "create_version"): "project.version.create",
    ("project", "create_web_variants"): "web.batch.create",
    ("project", "import_web_package"): "web.import",
    ("project", "import_interchange_timeline"): "timeline.interchange.import",
    ("project", "create_voiceover_cue"): "voiceover.cue.create",
    ("project", "update_voiceover_cue"): "voiceover.cue.update",
    ("project", "archive_voiceover_cue"): "voiceover.cue.archive",
    ("project", "add_voiceover_take"): "voiceover.take.add",
    ("project", "update_voiceover_take"): "voiceover.take.update",
    ("project", "select_voiceover_take"): "voiceover.take.select",
    ("project", "archive_voiceover_take"): "voiceover.take.archive",
    ("project", "place_voiceover_take"): "voiceover.take.place",
    ("project", "set_voiceover_latency_calibration"): "voiceover.latency.set",
    ("project", "analyze_voiceover_latency_calibration"): "voiceover.latency.analyze",
    ("project", "move_script_segment"): "script.segment.move",
    ("project", "remove_audio_effect"): "audio.effect.remove",
    ("project", "remove_web_keyframe"): "web.clip.keyframe.remove",
    ("project", "remove_web_parameter_keyframe"): "web.clip.parameter.keyframe.remove",
    ("project", "restore_version"): "project.version.restore",
    ("project", "save_audio_bus"): "audio.bus.update",
    ("project", "save_audio_effect"): "audio.effect.save",
    ("project", "select_web_variant"): "web.clip.variant.select",
    ("project", "set_web_field_locks"): "web.clip.lock.update",
    ("project", "set_web_keyframe"): "web.clip.keyframe.set",
    ("project", "set_web_parameter_keyframe"): "web.clip.parameter.keyframe.set",
    ("project", "set_web_parameter_lock"): "web.clip.parameter.lock.update",
    ("project", "update_subtitle_segment"): "subtitle.segment.update",
    ("project", "update_translation_segment_text"): "subtitle.segment.update",
    ("project", "update_web_clip"): "web.clip.update",
    ("project", "update_web_data"): "web.clip.data.update",
    ("project", "update_web_data_from_file"): "web.clip.data.snapshot",
    ("project", "update_web_parameter"): "web.clip.parameter.update",
    ("project", "update_web_theme"): "web.clip.theme.update",
    ("timeline", "add_clip"): "timeline.clip.add",
    ("timeline", "add_clips"): "timeline.clip.batch.add",
    ("timeline", "add_clip_visual_effect"): "timeline.clip.effect.add",
    ("timeline", "add_clip_mask"): "timeline.clip.mask.add",
    ("timeline", "add_review_thread"): "review.thread.create",
    ("timeline", "create_multicam_group"): "multicam.group.create",
    ("timeline", "switch_multicam_angle"): "multicam.angle.switch",
    ("timeline", "archive_review_thread"): "review.thread.archive",
    ("timeline", "assign_clip_visual_effect_mask"): "timeline.clip.effect.mask.assign",
    ("timeline", "upsert_clip_effect_parameter_keyframe"): "timeline.clip.effect.keyframe.set",
    ("timeline", "remove_clip_effect_parameter_keyframe"): "timeline.clip.effect.keyframe.remove",
    ("timeline", "move_clip_effect_parameter_keyframe"): "timeline.clip.effect.keyframe.move",
    ("timeline", "retime_clip_effect_parameter_keyframes"): "timeline.clip.effect.keyframe.retime",
    ("timeline", "retime_clip_mask_keyframes"): "timeline.clip.mask.keyframe.retime",
    ("timeline", "add_marker"): "timeline.marker.add",
    ("timeline", "add_track"): "timeline.track.add",
    ("timeline", "copy_clip"): "timeline.clip.copy",
    ("timeline", "create_transition"): "timeline.transition.add",
    ("timeline", "delete_clip"): "timeline.clip.delete",
    ("timeline", "move_clip"): "timeline.clip.move",
    ("timeline", "move_clip_visual_effect"): "timeline.clip.effect.move",
    ("timeline", "move_clip_mask_keyframe"): "timeline.clip.mask.keyframe.move",
    ("timeline", "move_clip_mask"): "timeline.clip.mask.move",
    ("timeline", "remove_clip_visual_effect"): "timeline.clip.effect.remove",
    ("timeline", "remove_clip_mask"): "timeline.clip.mask.remove",
    ("timeline", "remove_clip_mask_keyframe"): "timeline.clip.mask.keyframe.remove",
    ("timeline", "remove_marker"): "timeline.marker.remove",
    ("timeline", "remove_transition"): "timeline.transition.remove",
    ("timeline", "reopen_review_thread"): "review.thread.reopen",
    ("timeline", "reply_review_thread"): "review.thread.reply",
    ("timeline", "resolve_review_thread"): "review.thread.resolve",
    ("timeline", "restore_review_thread"): "review.thread.restore",
    ("timeline", "replace_clip_source"): "timeline.clip.source.replace",
    ("timeline", "set_clip_audio"): "timeline.clip.audio",
    ("timeline", "set_clip_transform"): "timeline.clip.transform",
    ("timeline", "upsert_clip_mask_keyframe"): "timeline.clip.mask.keyframe.set",
    ("timeline", "upsert_clip_transform_keyframe"): "timeline.clip.transform.keyframe.set",
    ("timeline", "remove_clip_transform_keyframe"): "timeline.clip.transform.keyframe.remove",
    ("timeline", "move_clip_transform_keyframe"): "timeline.clip.transform.keyframe.move",
    ("timeline", "retime_clip_transform_keyframes"): "timeline.clip.transform.keyframe.retime",
    ("timeline", "set_subtitle_track_style"): "subtitle.track.style.update",
    ("timeline", "split_clip"): "timeline.clip.split",
    ("timeline", "update_clip_visual_effect"): "timeline.clip.effect.update",
    ("timeline", "update_clip_mask"): "timeline.clip.mask.update",
    ("timeline", "update_marker"): "timeline.marker.update",
    ("timeline", "update_transition"): "timeline.transition.update",
    ("timeline", "update_review_thread"): "review.thread.update",
    ("timeline", "edit_review_message"): "review.message.edit",
}

_WEB_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "commit_web_runtime_state": ("sequence_id", "clip_id"),
    "move_web_keyframe": (
        "sequence_id",
        "clip_id",
        "layer_id",
        "field",
        "old_time_ms",
        "new_time_ms",
    ),
    "move_web_parameter_keyframe": (
        "sequence_id",
        "clip_id",
        "parameter_id",
        "old_time_ms",
        "new_time_ms",
    ),
    "remove_web_keyframe": ("sequence_id", "clip_id", "layer_id", "field", "time_ms"),
    "remove_web_parameter_keyframe": (
        "sequence_id",
        "clip_id",
        "parameter_id",
        "time_ms",
    ),
    "select_web_variant": ("sequence_id", "clip_id", "variant_id"),
    "set_web_field_locks": ("sequence_id", "clip_id", "layer_id", "fields", "locked"),
    "set_web_keyframe": (
        "sequence_id",
        "clip_id",
        "layer_id",
        "field",
        "time_ms",
        "value",
    ),
    "set_web_parameter_keyframe": (
        "sequence_id",
        "clip_id",
        "parameter_id",
        "time_ms",
        "value",
    ),
    "set_web_parameter_lock": ("sequence_id", "clip_id", "parameter_id", "locked"),
    "update_web_clip": ("sequence_id", "clip_id", "updates"),
    "update_web_data": ("sequence_id", "clip_id", "values"),
    "update_web_data_from_file": ("sequence_id", "clip_id", "source"),
    "update_web_parameter": ("sequence_id", "clip_id", "parameter_id", "value"),
    "update_web_theme": ("sequence_id", "clip_id", "changes"),
}


def plan_desktop_project_mutation(
    target: DesktopMutationTarget,
    command: str,
    *,
    sequence_id: str,
    args: list[Any],
    kwargs: dict[str, Any],
    project: ProjectMutationDocuments | None = None,
) -> ProjectMutationPlan:
    operation = _OPERATION_ALIASES.get((target, command), f"{target}.{command}")
    arguments = _desktop_arguments(
        target,
        command,
        sequence_id=sequence_id,
        args=args,
        kwargs=kwargs,
    )
    return plan_automation_project_mutation(
        operation,
        arguments,
        default_sequence_id=sequence_id,
        project=project,
    )


def _desktop_arguments(
    target: DesktopMutationTarget,
    command: str,
    *,
    sequence_id: str,
    args: list[Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    arguments = dict(kwargs)
    if target == "timeline":
        arguments.setdefault("sequence_id", sequence_id)
        _bind_timeline_arguments(command, args, arguments)
        return arguments
    names = _WEB_ARGUMENTS.get(command)
    if names is not None:
        _bind_positional(names, args, arguments)
    if command in {"close_script_gap", "move_script_segment"}:
        _bind_positional(("sequence_id", "document_id", "segment_id"), args, arguments)
    arguments.setdefault("sequence_id", sequence_id)
    if command == "commit_web_asset_rebind":
        _bind_positional(("asset_id", "source", "plan_digest", "resolutions"), args, arguments)
    elif command in {"update_subtitle_segment", "update_translation_segment_text"}:
        _bind_positional(("document_id", "segment_id"), args, arguments)
    elif command.startswith(
        (
            "add_subtitle",
            "delete_subtitle",
            "merge_subtitle",
            "split_subtitle",
            "replace_subtitle",
            "replace_all_subtitle",
            "replace_selected_subtitle",
            "fix_subtitle",
            "smart_split_subtitle",
        )
    ):
        _bind_positional(("document_id",), args, arguments)
    elif command in {
        "reset_subtitle_placement_range",
        "update_subtitle_placement_range",
        "update_subtitle_placement_text",
    }:
        _bind_positional(("placement_id",), args, arguments)
    elif command == "save_audio_bus":
        bus = arguments.get("bus") or (args[0] if args else None)
        arguments["bus_id"] = _entity_id(bus)
        arguments.setdefault("changes", _model_changes(bus))
    elif command == "save_audio_effect":
        arguments["effect"] = arguments.get("effect") or (args[0] if args else None)
    elif command == "remove_audio_effect":
        _bind_positional(("effect_id",), args, arguments)
    elif command in {
        "update_dubbing_reference",
        "update_dubbing_speaker",
        "update_dubbing_utterance",
    }:
        _bind_positional(("session_id",), args, arguments)
    elif command == "commit_web_runtime_state":
        _bind_positional(("sequence_id", "clip_id"), args, arguments)
    elif command in {"apply_transcript_edit", "create_short_sequence"}:
        arguments.setdefault("sequence_id", sequence_id)
    elif command == "generate_sequence_variants":
        _bind_positional(("source_sequence_id", "specs"), args, arguments)
    elif command == "collect_project_assets":
        _bind_positional(("asset_ids",), args, arguments)
    elif command == "set_project_collection_state":
        _bind_positional(("collection_id",), args, arguments)
    return arguments


def _bind_timeline_arguments(command: str, args: list[Any], arguments: dict[str, Any]) -> None:
    if command == "add_clip":
        return
    if command in {"move_clips", "delete_clips", "set_clips_properties", "create_compound_clip"}:
        _bind_positional(("clip_ids",), args, arguments)
        return
    if command in {
        "copy_clip",
        "delete_clip",
        "detach_clip_audio",
        "move_clip",
        "replace_clip_source",
        "replace_scene_markers",
        "set_clip_audio",
        "set_clip_speed",
        "set_clip_transform",
        "set_clip_transform_keyframes",
        "upsert_clip_transform_keyframe",
        "remove_clip_transform_keyframe",
        "move_clip_transform_keyframe",
        "retime_clip_transform_keyframes",
        "add_clip_mask",
        "split_clip",
        "trim_clip",
    }:
        _bind_positional(("clip_id",), args, arguments)
        return
    if command in {
        "add_clip_visual_effect",
        "move_clip_visual_effect",
        "remove_clip_visual_effect",
        "update_clip_visual_effect",
        "upsert_clip_effect_parameter_keyframe",
        "remove_clip_effect_parameter_keyframe",
        "move_clip_effect_parameter_keyframe",
        "retime_clip_effect_parameter_keyframes",
        "set_clip_effect_parameter_keyframes",
        "assign_clip_visual_effect_mask",
    }:
        _bind_positional(("clip_id", "effect_id"), args, arguments)
        return
    if command in {
        "update_clip_mask",
        "remove_clip_mask",
        "upsert_clip_mask_keyframe",
        "remove_clip_mask_keyframe",
        "move_clip_mask_keyframe",
        "retime_clip_mask_keyframes",
        "set_clip_mask_keyframes",
    }:
        _bind_positional(("clip_id", "mask_id"), args, arguments)
        return
    if command == "create_transition":
        _bind_positional(("left_clip_id", "right_clip_id"), args, arguments)
        return
    if command in {"update_transition", "remove_transition"}:
        _bind_positional(("transition_id",), args, arguments)
        return
    if command in {"update_marker", "remove_marker"}:
        _bind_positional(("marker_id",), args, arguments)
        return
    if command == "create_multicam_group":
        _bind_positional(("name", "angles"), args, arguments)
        return
    if command == "switch_multicam_angle":
        _bind_positional(("group_id", "angle_id", "timeline_frame"), args, arguments)
        return
    if command in {
        "archive_review_thread",
        "edit_review_message",
        "reopen_review_thread",
        "reply_review_thread",
        "resolve_review_thread",
        "restore_review_thread",
        "update_review_thread",
    }:
        _bind_positional(("thread_id",), args, arguments)
        return
    if command in {"update_range", "remove_range"}:
        _bind_positional(("range_id",), args, arguments)
        return
    if command in {
        "set_track_state",
        "set_subtitle_track_style",
        "set_primary_dialogue_track",
        "move_track",
    }:
        _bind_positional(("track_id",), args, arguments)
        return
    if command == "dissolve_compound_clip":
        _bind_positional(("compound_id",), args, arguments)
        return
    if command == "set_web_clip_state":
        state = arguments.get("web_state") or (args[0] if args else None)
        arguments["clip_id"] = _field_value(state, "clip_id")


def _bind_positional(names: tuple[str, ...], args: list[Any], arguments: dict[str, Any]) -> None:
    for index, name in enumerate(names):
        if name not in arguments and index < len(args):
            arguments[name] = args[index]


def _model_changes(value: Any) -> dict[str, Any]:
    if isinstance(value, DomainModel):
        return value.model_dump(mode="python", exclude_computed_fields=True)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _entity_id(value: Any) -> Any:
    if isinstance(value, DomainModel):
        return getattr(value, "id", None)
    if isinstance(value, Mapping):
        return value.get("id")
    return getattr(value, "id", None)


def _field_value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)
