from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from mediaflow.domain.collaboration import ProjectMutationPlan
from mediaflow.domain.model_base import DomainModel


class ProjectMutationDocuments(Protocol):
    def get_project(self) -> Any: ...

    def describe_web_clip_editing(
        self,
        sequence_id: str,
        clip_id: str,
        *,
        scene_id: str | None = None,
    ) -> Any: ...


def plan_automation_project_mutation(
    operation: str,
    arguments: dict[str, Any],
    *,
    default_sequence_id: str,
    project: ProjectMutationDocuments | None = None,
) -> ProjectMutationPlan:
    return _plan_project_mutation(
        operation,
        arguments,
        default_sequence_id=default_sequence_id,
        project=project,
    )


def _plan_project_mutation(
    operation: str,
    arguments: dict[str, Any],
    *,
    default_sequence_id: str,
    project: ProjectMutationDocuments | None,
) -> ProjectMutationPlan:
    scopes = _change_scopes(
        operation,
        arguments,
        default_sequence_id=default_sequence_id,
    )
    conflicts = _conflict_set(
        operation,
        arguments,
        default_sequence_id=default_sequence_id,
        change_scopes=scopes,
        project=project,
    )
    return ProjectMutationPlan.scoped(scopes, conflict_set=conflicts)


def _change_scopes(
    operation: str,
    arguments: dict[str, Any],
    *,
    default_sequence_id: str,
) -> list[str]:
    sequence = _path(arguments.get("sequence_id") or default_sequence_id)
    sequence_root = f"/sequences/{sequence}"
    if operation == "project.create":
        return ["/project"]
    if operation in {"project.upgrade", "project.version.restore"}:
        return ["/project"]
    if operation == "project.version.create":
        return ["/project/versions"]
    if operation == "asset.import":
        return ["/assets"]
    if operation == "sequence.short.create":
        return ["/sequences"]
    if operation == "timeline.portable.import":
        return ["/assets", sequence_root, "/subtitles", "/highlights"]
    if operation.startswith("timeline."):
        if operation == "timeline.set_sequence_profile":
            return [sequence_root, "/assets", "/subtitles", "/highlights"]
        if operation == "timeline.set_web_clip_state":
            return [f"/web/clips/{_path(arguments.get('clip_id'))}"]
        if operation in {"timeline.clip.move", "timeline.move_clips"}:
            return _timeline_move_change_scopes(
                arguments,
                sequence_root=sequence_root,
            )
        return [sequence_root]
    if operation == "subtitle.track.style.update":
        return [sequence_root]
    if operation == "subtitle.segment.update":
        document_root = f"/subtitles/documents/{_path(arguments.get('document_id'))}"
        root = f"{document_root}/segments/{_path(arguments.get('segment_id'))}"
        return [root, f"{document_root}/words"]
    if operation == "transcript.edit.apply":
        return [sequence_root, "/subtitles"]
    if operation.startswith("script.segment.") or operation == "script.gap.close":
        scopes = [f"/subtitles/documents/{_path(arguments.get('document_id'))}"]
        return ([sequence_root] if operation in {"script.segment.move", "script.gap.close"} else []) + scopes
    if operation == "transcript.sequence.transcribe":
        return [f"/tasks/transcript-sequence:{sequence}"]
    if operation == "diagnostics.bundle.create":
        return ["/tasks/diagnostics-bundle"]
    if operation == "audio.bus.update":
        root = f"/audio/buses/{_path(arguments.get('bus_id'))}"
        return _field_paths(root, arguments.get("changes"))
    if operation == "audio.effect.save":
        return [f"/audio/effects/{_path(_entity_id(arguments.get('effect')), 'new')}"]
    if operation == "audio.effect.remove":
        return [f"/audio/effects/{_path(arguments.get('effect_id'))}"]
    if operation == "project.save_audio_effect_chain":
        return ["/audio"]
    if operation.startswith("task."):
        return [f"/tasks/{_path(arguments.get('task_id'), 'new')}"]
    if operation.startswith("dubbing."):
        return [f"/dubbing/sessions/{_path(arguments.get('session_id'), 'new')}"]
    if operation.startswith(("preview.", "export.")):
        return [f"/tasks/{operation.replace('.', '-')}:{sequence}"]
    if operation == "web.import":
        return ["/assets"]
    if operation.startswith("web.clip."):
        return [f"/web/clips/{_path(arguments.get('clip_id'))}"]
    if operation.startswith("web.batch."):
        return ["/sequences"]
    if operation == "web.asset.rebind.commit":
        return [f"/assets/{_path(arguments.get('asset_id'))}", "/web/clips"]
    return _desktop_only_change_scopes(operation, arguments, sequence_root=sequence_root)


def _desktop_only_change_scopes(
    operation: str,
    arguments: dict[str, Any],
    *,
    sequence_root: str,
) -> list[str]:
    if operation == "project.apply_subtitle_placement_to_document":
        return ["/subtitles"]
    if operation in {
        "project.reset_subtitle_placement_range",
        "project.update_subtitle_placement_range",
        "project.update_subtitle_placement_text",
    }:
        return [f"/subtitles/placements/{_path(arguments.get('placement_id'))}"]
    if operation.startswith(
        (
            "project.add_subtitle",
            "project.delete_subtitle",
            "project.merge_subtitle",
            "project.split_subtitle",
            "project.replace_subtitle",
            "project.replace_all_subtitle",
            "project.replace_selected_subtitle",
            "project.fix_subtitle",
            "project.smart_split_subtitle",
        )
    ):
        return [f"/subtitles/documents/{_path(arguments.get('document_id'))}"]
    if operation == "project.adopt_main_profile_from_video":
        return ["/project"]
    if operation == "project.rename_project":
        return ["/project/name"]
    if operation == "project.populate_sample_project":
        return ["/project"]
    if operation == "project.set_workflow_mode":
        return ["/project/workflow_auto_continue"]
    if operation in {
        "project.create_highlight_short",
        "project.create_short_from_bounds",
        "project.create_short_from_range",
    }:
        return ["/sequences"]
    if operation == "project.place_subtitle_document":
        return ["/subtitles"]
    if operation == "project.create_asset_bin":
        return ["/asset-bins"]
    if "highlight" in operation:
        return ["/highlights"]
    if "task" in operation or "workflow" in operation:
        return ["/tasks"]
    if "asset" in operation or operation.startswith(
        ("project.import_", "project.relink_", "project.capture_")
    ):
        return ["/assets"]
    if "sequence" in operation:
        return ["/sequences"]
    if operation.startswith("project.save_audio"):
        return ["/audio"]
    if "dubbing" in operation:
        return [f"/dubbing/sessions/{_path(arguments.get('session_id'), 'new')}"]
    if operation.startswith("project.") and "web" in operation:
        return [f"/web/clips/{_path(arguments.get('clip_id'))}"]
    raise RuntimeError(f"Project write operation has no mutation boundary: {operation}")


def _timeline_move_change_scopes(
    arguments: dict[str, Any],
    *,
    sequence_root: str,
) -> list[str]:
    clip_ids = arguments.get("clip_ids") or [arguments.get("clip_id")]
    clip_scopes = [
        f"{sequence_root}/clips/{_path(clip_id)}" for clip_id in dict.fromkeys(clip_ids) if clip_id
    ]
    return [
        *(clip_scopes or [f"{sequence_root}/clips"]),
        f"{sequence_root}/tracks",
        f"{sequence_root}/transitions",
    ]


def _conflict_set(
    operation: str,
    arguments: dict[str, Any],
    *,
    default_sequence_id: str,
    change_scopes: list[str],
    project: ProjectMutationDocuments | None,
) -> list[str]:
    sequence = _path(arguments.get("sequence_id") or default_sequence_id)
    root = f"/sequences/{sequence}"
    if operation == "timeline.track.add":
        return [f"{root}/tracks/create"]
    if operation in {"timeline.clip.add", "timeline.clip.batch.add"}:
        track_id = arguments.get("track_id")
        return [
            f"{root}/clips/create",
            *([f"{root}/tracks/{_path(track_id)}"] if track_id else []),
        ]
    if operation in {
        "timeline.clip.move",
        "timeline.clip.copy",
        "timeline.clip.split",
        "timeline.clip.delete",
        "timeline.clip.freeze.add",
        "timeline.clip.transform",
        "timeline.clip.audio",
        "timeline.clip.source.replace",
        "timeline.clip.effect.add",
        "timeline.clip.effect.update",
        "timeline.clip.effect.move",
        "timeline.clip.effect.remove",
        "timeline.trim_clip",
        "timeline.detach_clip_audio",
        "timeline.set_clip_speed",
        "timeline.set_clip_transform_keyframes",
        "timeline.replace_scene_markers",
    }:
        clip_id = arguments.get("clip_id")
        if not clip_id:
            return [f"{root}/clips"]
        conflicts = [f"{root}/clips/{_path(clip_id)}"]
        track_id = arguments.get("track_id")
        if track_id:
            conflicts.append(f"{root}/tracks/{_path(track_id)}")
        return conflicts
    if operation in {
        "timeline.move_clips",
        "timeline.delete_clips",
        "timeline.set_clips_properties",
        "timeline.create_compound_clip",
    }:
        clip_ids = arguments.get("clip_ids") or []
        return [f"{root}/clips/{_path(clip_id)}" for clip_id in clip_ids] or [f"{root}/clips"]
    if operation.startswith("timeline.transition."):
        transition_id = arguments.get("transition_id")
        return [
            f"{root}/transitions/{_path(transition_id)}" if transition_id else f"{root}/transitions/create"
        ]
    if operation.startswith("timeline.marker."):
        marker_id = arguments.get("marker_id")
        return [f"{root}/markers/{_path(marker_id)}" if marker_id else f"{root}/markers/create"]
    if operation in {"timeline.update_range", "timeline.remove_range"}:
        return [f"{root}/ranges/{_path(arguments.get('range_id'))}"]
    if operation == "timeline.add_range":
        return [f"{root}/ranges/create"]
    if operation in {
        "timeline.set_primary_dialogue_track",
        "timeline.set_track_state",
        "timeline.move_track",
    }:
        return [f"{root}/tracks/{_path(arguments.get('track_id'))}"]
    if operation == "timeline.dissolve_compound_clip":
        return [f"{root}/compounds/{_path(arguments.get('compound_id'))}"]
    if operation == "subtitle.track.style.update":
        track_id = arguments.get("track_id")
        if not track_id:
            return [f"{root}/tracks"]
        return [f"{root}/tracks/{_path(track_id)}"]
    if operation == "subtitle.segment.update":
        segment_root = (
            f"/subtitles/documents/{_path(arguments.get('document_id'))}"
            f"/segments/{_path(arguments.get('segment_id'))}"
        )
        fields = {
            name: value
            for name, value in arguments.items()
            if name not in {"document_id", "segment_id", "sequence_id"}
        }
        return _field_paths(segment_root, fields)
    if operation.startswith("web.clip."):
        return _web_clip_conflict_set(
            operation,
            arguments,
            project=project,
            default_sequence_id=default_sequence_id,
        )
    return change_scopes


def _web_clip_conflict_set(
    operation: str,
    arguments: dict[str, Any],
    *,
    project: ProjectMutationDocuments | None,
    default_sequence_id: str,
) -> list[str]:
    root = f"/web/clips/{_path(arguments.get('clip_id'))}"
    scene_id = _path(arguments.get("scene_id"))
    if operation == "web.clip.variant.select":
        return [f"{root}/variant"]
    if operation == "web.clip.theme.update":
        return _field_paths(f"{root}/theme", arguments.get("changes"))
    if operation == "web.clip.data.update":
        return _field_paths(
            f"{root}/scenes/{scene_id}/data_snapshot/values",
            arguments.get("values"),
        )
    if operation == "web.clip.data.snapshot":
        return [f"{root}/scenes/{scene_id}/data_snapshot"]
    if operation == "web.clip.lock.update":
        return [f"{root}/scenes/{scene_id}/locks/{_path(arguments.get('layer_id'))}"]
    if operation in {
        "web.clip.keyframe.set",
        "web.clip.keyframe.remove",
        "project.move_web_keyframe",
    }:
        return [
            f"{root}/scenes/{scene_id}/animations/"
            f"{_path(arguments.get('layer_id'))}/{_path(arguments.get('field'))}"
        ]
    if operation in {
        "web.clip.parameter.keyframe.set",
        "web.clip.parameter.keyframe.remove",
        "project.move_web_parameter_keyframe",
    }:
        return [f"{root}/scenes/{scene_id}/parameter_animations/{_path(arguments.get('parameter_id'))}"]
    if operation in {"web.clip.parameter.update", "web.clip.parameter.lock.update"}:
        if project is None:
            return [root]
        field_path = _web_parameter_field_path(
            arguments,
            project=project,
            default_sequence_id=default_sequence_id,
        )
        if operation == "web.clip.parameter.update":
            return [f"{root}/{field_path}"]
        segments = field_path.split("/")
        lock_root = (
            "parameter_locks" if segments[0] == "parameters" else f"scenes/{segments[1]}/parameter_locks"
        )
        return [f"{root}/{lock_root}"]
    if operation == "web.clip.update":
        scene_root = f"{root}/scenes/{scene_id}/layers"
        updates = arguments.get("updates")
        if isinstance(updates, Mapping):
            paths = [
                f"{scene_root}/{_path(layer_id)}/{_path(field)}"
                for layer_id, fields in updates.items()
                if isinstance(fields, Mapping)
                for field in fields
            ]
            if paths:
                return paths
    return [root]


def _web_parameter_field_path(
    arguments: dict[str, Any],
    *,
    project: ProjectMutationDocuments | None,
    default_sequence_id: str,
) -> str:
    if project is None:
        raise RuntimeError("Web parameter conflict planning requires an open project")
    parameter_id = str(arguments.get("parameter_id") or "")
    document = project.describe_web_clip_editing(
        str(arguments.get("sequence_id") or default_sequence_id),
        str(arguments.get("clip_id") or ""),
        scene_id=(str(arguments["scene_id"]) if arguments.get("scene_id") else None),
    )
    fields = document.fields if hasattr(document, "fields") else document.get("fields", [])
    try:
        field = next(
            item
            for item in fields
            if _field_value(item, "target") == "parameter" and _field_value(item, "source_id") == parameter_id
        )
    except StopIteration as error:
        raise ValueError(f"Editable media parameter does not exist: {parameter_id}") from error
    return "/".join(_path(segment) for segment in str(_field_value(field, "path")).split("."))


def _field_paths(root: str, values: Any) -> list[str]:
    if isinstance(values, DomainModel):
        values = values.model_dump(mode="python", exclude_unset=True)
    if not isinstance(values, Mapping) or not values:
        return [root]
    return [f"{root}/{_path(name)}" for name in sorted(values)]


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


def _path(value: Any, fallback: str = "main") -> str:
    return str(value or fallback).replace("~", "~0").replace("/", "~1")
