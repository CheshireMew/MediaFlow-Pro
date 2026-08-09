from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CommandAccess = Literal["read", "write", "runtime"]
HistoryMode = Literal["reversible", "non_undoable"]


@dataclass(frozen=True, slots=True)
class DesktopCommand:
    access: CommandAccess
    history_mode: HistoryMode = "non_undoable"


PROJECT_READ_COMMANDS = frozenset(
    {
        "active_workflow",
        "content_revision",
        "committed_task_result",
        "describe_web_clip_editing",
        "diff_web_clip_update",
        "find_subtitle_matches",
        "get_asset",
        "get_project",
        "get_sequence",
        "get_subtitle_document",
        "get_subtitle_placement",
        "get_task",
        "get_web_asset_spec",
        "get_web_clip",
        "inspect_transcript",
        "inspect_web_asset",
        "list_asset_bins",
        "list_assets",
        "list_audio_buses",
        "list_audio_effects",
        "list_export_history",
        "list_highlights",
        "list_project_events",
        "list_project_events_after_revision",
        "list_sequences",
        "list_subtitle_documents",
        "list_subtitle_placements",
        "list_subtitle_segments",
        "list_subtitle_words",
        "list_tasks",
        "list_versions",
        "list_web_assets",
        "list_workflow_runs",
        "load_timeline",
        "loudness_snapshot_hash",
        "preview_transcript_edit",
        "proxy_decision",
        "read_loudness_metrics",
        "read_web_variant_records",
        "resolve_asset_path",
        "selected_highlights",
        "selected_subtitle_segments_srt",
        "sequence_boundary_snapshot_hash",
        "subtitle_segment_summary",
        "suggested_profile",
        "task_events_after",
        "task_snapshot",
        "web_render_cache_ready",
        "web_runtime_state",
        "wait_for_task",
    }
)

PROJECT_RUNTIME_COMMANDS = frozenset(
    {
        "attach_export_task",
        "begin_download_workflow",
        "cancel_all_tasks",
        "cancel_task",
        "cancel_workflow",
        "clear_task_history",
        "close_web_preview",
        "continue_workflow",
        "delete_task",
        "export_fcpxml",
        "export_web_clip",
        "import_asset",
        "prepare_web_sequence",
        "pause_all_tasks",
        "pause_task",
        "reconcile_workflow",
        "resume_task",
        "retry_task",
        "skip_workflow",
        "start_task",
        "web_editor_entry_url",
        "write_subtitle_srt",
    }
)

PROJECT_WRITE_COMMANDS = frozenset(
    {
        "add_manual_highlight",
        "add_subtitle_segment",
        "adopt_main_profile_from_video",
        "apply_subtitle_placement_to_document",
        "apply_transcript_edit",
        "archive_short_sequence",
        "attach_export_task",
        "begin_download_workflow",
        "cancel_all_tasks",
        "cancel_task",
        "cancel_workflow",
        "capture_asset_frame",
        "clear_task_history",
        "commit_web_asset_rebind",
        "commit_web_runtime_state",
        "continue_workflow",
        "create_asset_bin",
        "create_highlight_short",
        "create_short_from_bounds",
        "create_short_from_range",
        "create_short_sequence",
        "create_version",
        "create_web_variants",
        "delete_highlight",
        "delete_subtitle_segments",
        "delete_task",
        "fix_subtitle_overlaps",
        "import_asset",
        "import_external_asset",
        "import_web_package",
        "merge_subtitle_segments",
        "move_assets_to_bin",
        "pause_all_tasks",
        "pause_task",
        "place_subtitle_document",
        "plan_web_asset_rebind",
        "populate_sample_project",
        "reconcile_workflow",
        "refresh_workflow_mode",
        "relink_asset",
        "relink_offline_assets",
        "replace_all_subtitle_text",
        "replace_selected_subtitle_texts",
        "replace_subtitle_match",
        "remove_audio_effect",
        "remove_web_keyframe",
        "remove_web_parameter_keyframe",
        "reset_subtitle_placement_range",
        "restore_version",
        "resume_task",
        "retry_task",
        "save_audio_bus",
        "save_audio_effect",
        "save_audio_effect_chain",
        "save_sequence_export_preset",
        "select_web_variant",
        "set_highlight_selected",
        "set_web_field_locks",
        "set_web_keyframe",
        "set_web_parameter_keyframe",
        "set_web_parameter_lock",
        "set_workflow_mode",
        "skip_workflow",
        "smart_split_subtitle_document",
        "split_subtitle_segment",
        "start_task",
        "update_highlight",
        "update_settings",
        "update_subtitle_placement_range",
        "update_subtitle_placement_text",
        "update_subtitle_segment",
        "update_web_clip",
        "update_web_data",
        "update_web_data_from_file",
        "update_web_parameter",
        "update_web_theme",
        "move_web_keyframe",
        "move_web_parameter_keyframe",
    }
)

TIMELINE_READ_COMMANDS = frozenset(
    {
        "preview_move_clips",
        "preview_ripple_delete_intervals",
        "reload",
        "snap_frame",
        "state",
    }
)

TIMELINE_WRITE_COMMANDS = frozenset(
    {
        "add_clip",
        "add_clips",
        "add_clip_visual_effect",
        "add_marker",
        "add_range",
        "add_track",
        "apply_ripple_delete_intervals",
        "clear_sequence_in_out",
        "copy_clip",
        "create_compound_clip",
        "create_transition",
        "delete_clip",
        "delete_clips",
        "detach_clip_audio",
        "dissolve_compound_clip",
        "move_clip",
        "move_clips",
        "move_clip_visual_effect",
        "move_track",
        "remove_clip_visual_effect",
        "remove_marker",
        "remove_range",
        "remove_transition",
        "replace_clip_source",
        "replace_scene_markers",
        "set_clip_audio",
        "set_clip_speed",
        "set_clip_transform",
        "set_clip_transform_keyframes",
        "set_clips_properties",
        "set_primary_dialogue_track",
        "set_sequence_in_out",
        "set_sequence_profile",
        "set_subtitle_track_style",
        "set_track_state",
        "set_web_clip_state",
        "split_clip",
        "trim_clip",
        "update_clip_visual_effect",
        "update_marker",
        "update_range",
        "update_transition",
    }
)

PROJECT_REVERSIBLE_COMMANDS = frozenset(
    {
        "add_subtitle_segment",
        "apply_transcript_edit",
        "archive_short_sequence",
        "delete_subtitle_segments",
        "fix_subtitle_overlaps",
        "merge_subtitle_segments",
        "replace_all_subtitle_text",
        "replace_selected_subtitle_texts",
        "replace_subtitle_match",
        "reset_subtitle_placement_range",
        "smart_split_subtitle_document",
        "split_subtitle_segment",
        "update_subtitle_placement_range",
        "update_subtitle_segment",
    }
)

TIMELINE_REVERSIBLE_COMMANDS = TIMELINE_WRITE_COMMANDS


def project_command(name: str) -> DesktopCommand:
    if name in PROJECT_READ_COMMANDS:
        return DesktopCommand("read")
    if name in PROJECT_RUNTIME_COMMANDS:
        return DesktopCommand("runtime")
    if name in PROJECT_WRITE_COMMANDS:
        return DesktopCommand(
            "write",
            "reversible" if name in PROJECT_REVERSIBLE_COMMANDS else "non_undoable",
        )
    raise ValueError(f"Unknown desktop project command: {name}")


def timeline_command(name: str) -> DesktopCommand:
    if name in TIMELINE_READ_COMMANDS:
        return DesktopCommand("read")
    if name in TIMELINE_WRITE_COMMANDS:
        return DesktopCommand("write", "reversible")
    raise ValueError(f"Unknown desktop timeline command: {name}")


def command_write_set(
    *,
    target: str,
    command: str,
    sequence_id: str,
    args: list[Any],
    kwargs: dict[str, Any],
) -> list[str]:
    if target == "timeline":
        root = f"/sequences/{_path(sequence_id)}"
        if "clip" in command:
            identifiers = _identifiers(args, kwargs, "clip_id", "clip_ids")
            return [f"{root}/clips/{_path(item)}" for item in identifiers] or [f"{root}/clips"]
        if "track" in command:
            identifiers = _identifiers(args, kwargs, "track_id")
            return [f"{root}/tracks/{_path(item)}" for item in identifiers] or [f"{root}/tracks"]
        if "transition" in command:
            identifiers = _identifiers(args, kwargs, "transition_id")
            return [f"{root}/transitions/{_path(item)}" for item in identifiers] or [f"{root}/transitions"]
        if "marker" in command:
            identifiers = _identifiers(args, kwargs, "marker_id")
            return [f"{root}/markers/{_path(item)}" for item in identifiers] or [f"{root}/markers"]
        if "range" in command:
            identifiers = _identifiers(args, kwargs, "range_id")
            return [f"{root}/ranges/{_path(item)}" for item in identifiers] or [f"{root}/ranges"]
        return [root]
    if command in {
        "apply_subtitle_placement_to_document",
        "reset_subtitle_placement_range",
        "update_subtitle_placement_range",
        "update_subtitle_placement_text",
    }:
        placement_id = kwargs.get("placement_id") or (args[0] if args else None)
        return [f"/subtitles/placements/{_path(placement_id)}"]
    if command.startswith(
        (
            "add_subtitle",
            "delete_subtitle",
            "merge_subtitle",
            "split_subtitle",
            "update_subtitle",
            "replace_subtitle",
            "replace_all_subtitle",
            "replace_selected_subtitle",
            "fix_subtitle",
            "smart_split_subtitle",
            "apply_subtitle",
            "reset_subtitle",
        )
    ):
        document_id = kwargs.get("document_id") or (args[0] if args else None)
        root = f"/subtitles/documents/{_path(document_id)}"
        if command == "update_subtitle_segment":
            segment_id = kwargs.get("segment_id") or (args[1] if len(args) > 1 else None)
            fields = [
                name
                for name in ("start_frame", "end_frame", "text")
                if name in kwargs
            ]
            segment_root = f"{root}/segments/{_path(segment_id)}"
            return [f"{segment_root}/{name}" for name in fields] or [segment_root]
        return [root]
    if command.startswith(("save_audio", "remove_audio")):
        return ["/audio"]
    if "web" in command:
        clip_id = kwargs.get("clip_id") or (args[1] if len(args) > 1 else None)
        root = f"/web/clips/{_path(clip_id)}"
        if command == "update_web_parameter":
            path = kwargs.get("path") or (args[2] if len(args) > 2 else None)
            return [f"{root}/parameters/{_path(path)}"]
        if command == "update_web_theme":
            changes = kwargs.get("changes") or (args[2] if len(args) > 2 else {})
            return _mapping_paths(f"{root}/theme", changes)
        if command in {"update_web_data", "update_web_data_from_file"}:
            changes = kwargs.get("changes") or (args[2] if len(args) > 2 else {})
            return _mapping_paths(f"{root}/data", changes)
        if command == "update_web_clip":
            changes = kwargs.get("updates") or (args[2] if len(args) > 2 else {})
            paths = [
                f"{root}/layers/{_path(layer_id)}/{_path(field)}"
                for layer_id, values in (changes.items() if isinstance(changes, dict) else ())
                for field in (values if isinstance(values, dict) else ())
            ]
            return paths or [f"{root}/layers"]
        return [root]
    if "highlight" in command:
        return ["/highlights"]
    if "task" in command or "workflow" in command:
        return ["/tasks"]
    if "asset" in command or command.startswith(("import_", "relink_", "capture_")):
        return ["/assets"]
    if "sequence" in command or command in {"restore_version", "populate_sample_project"}:
        return ["/sequences"]
    return [f"/desktop/{command}"]


def _identifiers(
    args: list[Any],
    kwargs: dict[str, Any],
    *names: str,
) -> list[str]:
    values: list[Any] = []
    for name in names:
        value = kwargs.get(name)
        if value is not None:
            values.extend(value if isinstance(value, (list, tuple, set)) else [value])
    if not values and args and isinstance(args[0], str):
        values.append(args[0])
    return [str(item) for item in values if str(item)]


def _path(value: Any) -> str:
    return str(value or "main").replace("~", "~0").replace("/", "~1")


def _mapping_paths(root: str, value: Any) -> list[str]:
    if not isinstance(value, dict) or not value:
        return [root]
    return [f"{root}/{_path(name)}" for name in value]
