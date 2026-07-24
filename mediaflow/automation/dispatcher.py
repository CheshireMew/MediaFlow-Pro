from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import JsonValue, TypeAdapter

from mediaflow.automation.contracts import (
    MUTATING_OPERATIONS,
    OPERATION_SCHEMAS,
    AutomationRequest,
    describe_contract,
    validate_arguments,
)
from mediaflow.composition import EditorApplication
from mediaflow.domain.audio import AudioEffect
from mediaflow.domain.enums import ExportFormat, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.task_commands import (
    ExportSequenceCommand,
    RenderWebClipCommand,
    TaskCommand,
)
from mediaflow.domain.timeline import ClipAudio, ClipTransform
from mediaflow.domain.transcript_edits import TranscriptEditPlan, TranscriptEditRequest
from mediaflow.domain.web_media import WebExportFormat
from mediaflow.infrastructure.web_render_service import WebRenderService

_TASK_COMMAND_ADAPTER: TypeAdapter[TaskCommand] = TypeAdapter(TaskCommand)


def _actor(value: object) -> Literal["human", "automation"]:
    return cast(Literal["human", "automation"], str(value))


def _project_path(value: str | None) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("project is required")
    path = Path(text).expanduser()
    return path.parent if path.name == "project.mfp" else path


def _required(arguments: dict[str, Any], name: str) -> Any:
    if name not in arguments or arguments[name] is None or arguments[name] == "":
        raise ValueError(f"arguments.{name} is required")
    return arguments[name]


def _project_snapshot(project) -> dict[str, Any]:
    documents = project.documents
    return {
        "project": documents.get_project().model_dump(mode="json"),
        "path": str(project.project_dir),
        "read_only": project.read_only,
        "sequences": [item.model_dump(mode="json") for item in documents.list_sequences()],
        "assets": [item.model_dump(mode="json") for item in documents.list_assets()],
        "web_assets": [item.model_dump(mode="json") for item in documents.list_web_asset_specs()],
        "active_workflows": [
            item.model_dump(mode="json") for item in documents.list_workflow_runs(active_only=True)
        ],
        "tasks": [item.model_dump(mode="json") for item in project.tasks.list()],
    }


def _sequence_id(project, arguments: dict[str, Any]) -> str:
    return str(arguments.get("sequence_id") or project.documents.get_project().main_sequence_id)


def _task_result(project, task, timeout: float) -> dict[str, Any]:
    completed = project.tasks.wait(task.id, timeout=timeout)
    result = project.consume_task_result(completed)
    return {"task": completed.model_dump(mode="json"), "result": result.as_dict()}


def execute_request(
    request: dict[str, Any] | AutomationRequest,
    *,
    application: EditorApplication | None = None,
) -> dict[str, Any]:
    envelope = (
        request
        if isinstance(request, AutomationRequest)
        else AutomationRequest.model_validate(request)
    )
    operation = envelope.operation.strip()
    if operation == "describe":
        return describe_contract()
    if operation not in OPERATION_SCHEMAS:
        raise ValueError(f"Unknown operation: {operation}")
    arguments = envelope.arguments
    validate_arguments(operation, arguments)
    api = application or EditorApplication()

    if operation == "project.create":
        root = _project_path(envelope.project)
        with api.create_project(root, str(_required(arguments, "name"))) as project:
            return _project_snapshot(project)

    writable = operation in MUTATING_OPERATIONS
    with api.open_project(
        _project_path(envelope.project),
        writable=writable,
        cooperative=writable,
    ) as project:
        documents = project.documents

        if operation == "project.inspect":
            return _project_snapshot(project)
        if operation == "project.version.list":
            return {
                "versions": [
                    item.model_dump(mode="json")
                    for item in project.list_versions()
                ]
            }
        if operation == "project.version.restore":
            record = project.restore_version(
                str(_required(arguments, "version_id"))
            )
            return {
                "restored_version": record.model_dump(mode="json"),
                **_project_snapshot(project),
            }
        if operation == "asset.list":
            return {"assets": [item.model_dump(mode="json") for item in documents.list_assets()]}
        if operation == "asset.import":
            task = project.import_asset(str(_required(arguments, "source")))
            result = _task_result(project, task, float(arguments.get("timeout", 3600)))
            completed = result["task"]
            if completed["status"] != "completed":
                raise RuntimeError(completed.get("error") or "Asset import failed")
            imported_id = result["result"]["imported_asset_id"]
            return {
                **result,
                "asset": documents.get_asset(imported_id).model_dump(mode="json"),
            }
        if operation == "sequence.short.create":
            sequence = project.sequences.create_short_from_bounds(
                str(_required(arguments, "source_sequence_id")),
                int(_required(arguments, "start_frame")),
                int(_required(arguments, "end_frame")),
                name=str(arguments.get("name") or "短视频"),
            )
            return {"sequence": sequence.model_dump(mode="json")}

        sequence_id = _sequence_id(project, arguments)
        editor = project.timeline(sequence_id)
        if operation == "timeline.get":
            return {"timeline": editor.state.model_dump(mode="json")}
        if operation == "timeline.track.add":
            track = editor.add_track(
                TrackKind(str(_required(arguments, "kind"))),
                str(arguments["name"]) if arguments.get("name") else None,
            )
            return {"track": track.model_dump(mode="json")}
        if operation == "timeline.clip.add":
            clip = editor.add_clip(
                track_id=str(_required(arguments, "track_id")),
                asset_id=str(_required(arguments, "asset_id")),
                timeline_start=int(_required(arguments, "timeline_start")),
                source_in=int(_required(arguments, "source_in")),
                duration=int(_required(arguments, "duration")),
                speed_numerator=int(arguments.get("speed_numerator", 1)),
                speed_denominator=int(arguments.get("speed_denominator", 1)),
            )
            return {"clip": clip.model_dump(mode="json")}
        if operation == "timeline.clip.move":
            clip = editor.move_clip(
                str(_required(arguments, "clip_id")),
                timeline_start=int(_required(arguments, "timeline_start")),
                track_id=str(arguments["track_id"]) if arguments.get("track_id") else None,
            )
            return {"clip": clip.model_dump(mode="json")}
        if operation == "timeline.clip.copy":
            clip = editor.copy_clip(
                str(_required(arguments, "clip_id")),
                timeline_start=int(_required(arguments, "timeline_start")),
                track_id=str(arguments["track_id"]) if arguments.get("track_id") else None,
            )
            return {"clip": clip.model_dump(mode="json")}
        if operation == "timeline.clip.split":
            clips = editor.split_clip(
                str(_required(arguments, "clip_id")), int(_required(arguments, "split_frame"))
            )
            return {"clips": [clip.model_dump(mode="json") for clip in clips]}
        if operation == "timeline.clip.delete":
            editor.delete_clips(
                [str(value) for value in _required(arguments, "clip_ids")],
                ripple=bool(arguments.get("ripple", False)),
            )
            return {"timeline": editor.state.model_dump(mode="json")}
        if operation == "timeline.clip.transform":
            clip = editor.set_clip_transform(
                str(_required(arguments, "clip_id")),
                ClipTransform.model_validate(_required(arguments, "transform")),
            )
            return {"clip": clip.model_dump(mode="json")}
        if operation == "timeline.clip.audio":
            clip = editor.set_clip_audio(
                str(_required(arguments, "clip_id")),
                ClipAudio.model_validate(_required(arguments, "audio")),
            )
            return {"clip": clip.model_dump(mode="json")}
        if operation == "timeline.undo":
            return {"timeline": editor.undo().model_dump(mode="json")}
        if operation == "timeline.redo":
            return {"timeline": editor.redo().model_dump(mode="json")}

        if operation == "subtitle.list":
            documents_data = documents.list_subtitle_documents(sequence_id=sequence_id)
            return {
                "documents": [
                    {
                        **document.model_dump(mode="json"),
                        "segments": [
                            segment.model_dump(mode="json")
                            for segment in documents.list_subtitle_segments(document.id)
                        ],
                    }
                    for document in documents_data
                ]
            }
        if operation == "subtitle.segment.update":
            segment = project.subtitle_editing.update_segment(
                str(_required(arguments, "document_id")),
                str(_required(arguments, "segment_id")),
                start_frame=int(_required(arguments, "start_frame")),
                end_frame=int(_required(arguments, "end_frame")),
                text=str(_required(arguments, "text")),
            )
            return {"segment": segment.model_dump(mode="json")}
        if operation == "transcript.get":
            snapshot = project.transcript_editing.inspect_transcript(
                sequence_id,
                document_id=(
                    str(arguments["document_id"])
                    if arguments.get("document_id")
                    else None
                ),
            )
            return {"transcript": snapshot.model_dump(mode="json")}
        if operation == "transcript.edit.preview":
            edit = TranscriptEditRequest.model_validate(
                _required(arguments, "edit")
            )
            plan = project.transcript_editing.preview_plan(
                edit,
                project.timeline(edit.sequence_id),
            )
            return {"plan": plan.model_dump(mode="json")}
        if operation == "transcript.edit.apply":
            plan = TranscriptEditPlan.model_validate(
                _required(arguments, "plan")
            )
            if plan.warnings and not bool(arguments.get("accept_warnings", False)):
                raise ValueError(
                    "Transcript edit plan contains warnings; review them and set "
                    "arguments.accept_warnings=true to apply"
                )
            result = project.transcript_editing.apply_plan(
                plan,
                project.timeline(plan.sequence_id),
            )
            return {"edit": result.model_dump(mode="json")}
        if operation == "audio.inspect":
            buses = documents.list_audio_buses(sequence_id)
            return {
                "buses": [
                    {
                        **bus.model_dump(mode="json"),
                        "effects": [
                            effect.model_dump(mode="json")
                            for effect in documents.list_audio_effects(bus.id)
                        ],
                    }
                    for bus in buses
                ]
            }
        if operation == "audio.bus.update":
            bus_id = str(_required(arguments, "bus_id"))
            bus = next(item for item in documents.list_audio_buses(sequence_id) if item.id == bus_id)
            updated = documents.save_audio_bus(
                bus.model_copy(update=dict(_required(arguments, "changes")))
            )
            return {"bus": updated.model_dump(mode="json")}
        if operation == "audio.effect.save":
            effect = documents.save_audio_effect(
                AudioEffect.model_validate(_required(arguments, "effect"))
            )
            return {"effect": effect.model_dump(mode="json")}
        if operation == "audio.effect.remove":
            documents.remove_audio_effect(str(_required(arguments, "effect_id")))
            return {"removed": True}
        if operation == "preview.render":
            state = editor.state
            WebRenderService(documents, api.runtime_paths).ensure_sequence(state)
            path = api.write_preview_snapshot(
                project.project_dir,
                state,
                use_proxies=bool(arguments.get("use_proxies", True)),
                prefer_sdr_preview_proxy=True,
            )
            return {"preview_graph": str(path)}
        if operation == "export.sequence":
            preset_value = arguments.get("preset")
            command = ExportSequenceCommand(
                sequence_id=sequence_id,
                output_path=str(_required(arguments, "output_path")),
                format=ExportFormat(str(arguments.get("format", "h264"))),
                preset=ExportPreset.model_validate(preset_value) if preset_value else None,
            )
            return _task_result(
                project,
                project.start_task(command, sequence_id=sequence_id),
                float(arguments.get("timeout", 3600)),
            )
        if operation == "task.list":
            return {"tasks": [item.model_dump(mode="json") for item in project.tasks.list()]}
        if operation == "task.status":
            task = project.tasks.get(str(_required(arguments, "task_id")))
            return {"task": task.model_dump(mode="json")}
        if operation == "task.start":
            task_command = _TASK_COMMAND_ADAPTER.validate_python(
                _required(arguments, "task_command")
            )
            task = project.start_task(
                task_command,
                [str(value) for value in arguments.get("input_asset_ids") or []],
                sequence_id=sequence_id,
            )
            return _task_result(project, task, float(arguments.get("timeout", 3600)))
        if operation == "task.resume":
            task = project.tasks.resume(str(_required(arguments, "task_id")))
            return _task_result(project, task, float(arguments.get("timeout", 3600)))
        if operation == "web.import":
            asset = project.web.import_package(str(_required(arguments, "source")))
            return {
                "asset": asset.model_dump(mode="json"),
                "web_asset": project.web.inspect_asset(asset.id).model_dump(mode="json"),
            }
        if operation == "web.inspect":
            spec = project.web.inspect_asset(str(_required(arguments, "asset_id")))
            return {"web_asset": spec.model_dump(mode="json")}
        if operation == "web.clip.get":
            web_state = project.web.get_clip(str(_required(arguments, "clip_id")))
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.update":
            web_state = project.web.update_clip(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                dict(_required(arguments, "updates")),
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
                actor=_actor(arguments.get("actor", "automation")),
                layout_id=str(arguments["layout_id"]) if arguments.get("layout_id") else None,
            )
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.diff":
            diff = project.web.diff_clip_update(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                dict(_required(arguments, "updates")),
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
                actor=_actor(arguments.get("actor", "automation")),
                layout_id=str(arguments["layout_id"]) if arguments.get("layout_id") else None,
            )
            return {"diff": diff.model_dump(mode="json")}
        if operation == "web.clip.layout.select":
            web_state = project.web.select_layout(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                str(_required(arguments, "layout_id")) or None,
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
            )
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.keyframe.set":
            web_state = project.web.set_keyframe(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                str(_required(arguments, "layer_id")),
                str(_required(arguments, "field")),
                int(_required(arguments, "time_ms")),
                cast(JsonValue, arguments["value"]),
                easing=dict(arguments.get("easing") or {}),
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
                actor=_actor(arguments.get("actor", "automation")),
            )
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.keyframe.remove":
            web_state = project.web.remove_keyframe(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                str(_required(arguments, "layer_id")),
                str(_required(arguments, "field")),
                int(_required(arguments, "time_ms")),
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
            )
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.theme.update":
            web_state = project.web.update_theme(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                dict(_required(arguments, "changes")),
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
            )
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.data.update":
            web_state = project.web.update_data(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                dict(_required(arguments, "values")),
                source_kind=cast(
                    Literal["inline", "file", "api"],
                    str(arguments.get("source_kind", "inline")),
                ),
                source_label=str(arguments.get("source_label", "")),
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
            )
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.data.snapshot":
            web_state = project.web.update_data_from_file(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                str(_required(arguments, "source")),
                field_id=str(arguments["field_id"]) if arguments.get("field_id") else None,
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
            )
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.lock.update":
            web_state = project.web.set_field_locks(
                str(_required(arguments, "sequence_id")),
                str(_required(arguments, "clip_id")),
                str(_required(arguments, "layer_id")),
                [str(value) for value in _required(arguments, "fields")],
                bool(_required(arguments, "locked")),
                expected_revision=(
                    int(arguments["expected_revision"])
                    if arguments.get("expected_revision") is not None
                    else None
                ),
            )
            return {"web_clip_state": web_state.model_dump(mode="json")}
        if operation == "web.clip.render":
            render_command = RenderWebClipCommand(
                sequence_id=str(_required(arguments, "sequence_id")),
                clip_id=str(_required(arguments, "clip_id")),
            )
            return _task_result(
                project,
                project.start_task(render_command, sequence_id=render_command.sequence_id),
                float(arguments.get("timeout", 3600)),
            )
        if operation == "web.clip.export":
            sequence_id = str(_required(arguments, "sequence_id"))
            web_export = WebRenderService(documents, api.runtime_paths).export_clip(
                documents.load_timeline(sequence_id),
                str(_required(arguments, "clip_id")),
                str(_required(arguments, "output_path")),
                cast(WebExportFormat, str(_required(arguments, "format"))),
                time_ms=int(arguments.get("time_ms", 0)),
                background=str(arguments.get("background", "#000000")),
                overwrite=bool(arguments.get("overwrite", False)),
            )
            return {"export": web_export.model_dump(mode="json")}
        if operation == "web.batch.create":
            records_value = arguments.get("records")
            source_value = arguments.get("source")
            if (records_value is None) == (source_value is None):
                raise ValueError("web.batch.create requires exactly one of records or source")
            records: list[Mapping[str, object]]
            if records_value is not None:
                records = [
                    dict(value)
                    for value in cast(list[Mapping[str, object]], records_value)
                ]
            else:
                records = project.web.read_variant_records(str(source_value))
            variants = project.web.create_variants(
                str(_required(arguments, "source_sequence_id")),
                str(_required(arguments, "clip_id")),
                records,
                {
                    str(key): str(value)
                    for key, value in dict(_required(arguments, "bindings")).items()
                },
                name_template=str(arguments.get("name_template", "版本 {index}")),
                actor=_actor(arguments.get("actor", "automation")),
            )
            return {"variants": [item.model_dump(mode="json") for item in variants]}
        if operation == "web.asset.rebind":
            report = project.web.rebind_asset(
                str(_required(arguments, "asset_id")),
                str(_required(arguments, "source")),
                dry_run=bool(arguments.get("dry_run", True)),
                allow_conflicts=bool(arguments.get("allow_conflicts", False)),
            )
            return {"rebind": report.model_dump(mode="json")}
    raise RuntimeError(f"Operation was declared but not dispatched: {operation}")
