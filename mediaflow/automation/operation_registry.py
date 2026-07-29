from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from mediaflow.automation import language_audio_operations as language_audio
from mediaflow.automation import project_operations as project
from mediaflow.automation import task_operations as tasks
from mediaflow.automation import timeline_operations as timeline
from mediaflow.automation import web_operations as web
from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.transcript_edits import (
    TranscriptEditPlan,
    TranscriptEditRequest,
)

OpenMode = Literal["create", "read", "write"]
ExecutionMode = Literal["atomic", "task"]
OperationHandler = Callable[[OperationContext], dict]


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    arguments_schema: dict[str, Any]
    open_mode: OpenMode
    execution_mode: ExecutionMode
    handler: OperationHandler

    @property
    def mutates_project(self) -> bool:
        return self.open_mode != "read"


def _schema(
    required: Sequence[str] = (),
    **properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(required),
        "properties": properties,
    }


def _inline_model_schema(model: type[DomainModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value: Any) -> Any:
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.rsplit("/", 1)[-1]
            merged = {
                **definitions[name],
                **{
                    key: item
                    for key, item in value.items()
                    if key != "$ref"
                },
            }
            return resolve(merged)
        return {key: resolve(item) for key, item in value.items()}

    return resolve(schema)


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
NUMBER = {"type": "number"}
BOOLEAN = {"type": "boolean"}
OBJECT = {"type": "object"}
ARRAY_OF_STRINGS = {"type": "array", "items": STRING}
ARRAY_OF_OBJECTS = {"type": "array", "items": OBJECT}
ANY: dict[str, Any] = {}

TRANSCRIPT_EDIT_REQUEST = _inline_model_schema(TranscriptEditRequest)
TRANSCRIPT_EDIT_PLAN = _inline_model_schema(TranscriptEditPlan)


def _read(
    schema: dict[str, Any],
    handler: OperationHandler,
) -> OperationDefinition:
    return OperationDefinition(schema, "read", "atomic", handler)


def _write(
    schema: dict[str, Any],
    handler: OperationHandler,
    *,
    task_backed: bool = False,
) -> OperationDefinition:
    return OperationDefinition(
        schema,
        "write",
        "task" if task_backed else "atomic",
        handler,
    )


OPERATIONS: dict[str, OperationDefinition] = {
    "project.create": OperationDefinition(
        _schema(["name"], name=STRING),
        "create",
        "atomic",
        project.create_project,
    ),
    "project.inspect": _read(_schema(), project.inspect_project),
    "project.version.list": _read(_schema(), project.list_versions),
    "project.version.restore": _write(
        _schema(["version_id"], version_id=STRING),
        project.restore_version,
    ),
    "asset.list": _read(_schema(), project.list_assets),
    "asset.import": _write(
        _schema(["source"], source=STRING, timeout=NUMBER),
        project.import_asset,
        task_backed=True,
    ),
    "sequence.short.create": _write(
        _schema(
            ["source_sequence_id", "start_frame", "end_frame"],
            source_sequence_id=STRING,
            start_frame=INTEGER,
            end_frame=INTEGER,
            name=STRING,
        ),
        project.create_short_sequence,
    ),
    "timeline.get": _read(
        _schema(sequence_id=STRING),
        timeline.get_timeline,
    ),
    "timeline.track.add": _write(
        _schema(
            ["kind"],
            sequence_id=STRING,
            kind={"enum": ["video", "audio", "subtitle"]},
            name=STRING,
        ),
        timeline.add_track,
    ),
    "timeline.clip.add": _write(
        _schema(
            [
                "track_id",
                "asset_id",
                "timeline_start",
                "source_in",
                "duration",
            ],
            sequence_id=STRING,
            track_id=STRING,
            asset_id=STRING,
            timeline_start=INTEGER,
            source_in=INTEGER,
            duration=INTEGER,
            speed_numerator=INTEGER,
            speed_denominator=INTEGER,
        ),
        timeline.add_clip,
    ),
    "timeline.clip.move": _write(
        _schema(
            ["clip_id", "timeline_start"],
            sequence_id=STRING,
            clip_id=STRING,
            timeline_start=INTEGER,
            track_id=STRING,
        ),
        timeline.move_clip,
    ),
    "timeline.clip.copy": _write(
        _schema(
            ["clip_id", "timeline_start"],
            sequence_id=STRING,
            clip_id=STRING,
            timeline_start=INTEGER,
            track_id=STRING,
        ),
        timeline.copy_clip,
    ),
    "timeline.clip.split": _write(
        _schema(
            ["clip_id", "split_frame"],
            sequence_id=STRING,
            clip_id=STRING,
            split_frame=INTEGER,
        ),
        timeline.split_clip,
    ),
    "timeline.clip.delete": _write(
        _schema(
            ["clip_ids"],
            sequence_id=STRING,
            clip_ids=ARRAY_OF_STRINGS,
            ripple=BOOLEAN,
        ),
        timeline.delete_clips,
    ),
    "timeline.clip.transform": _write(
        _schema(
            ["clip_id", "transform"],
            sequence_id=STRING,
            clip_id=STRING,
            transform=OBJECT,
        ),
        timeline.transform_clip,
    ),
    "timeline.clip.audio": _write(
        _schema(
            ["clip_id", "audio"],
            sequence_id=STRING,
            clip_id=STRING,
            audio=OBJECT,
        ),
        timeline.update_clip_audio,
    ),
    "timeline.undo": _write(
        _schema(sequence_id=STRING),
        timeline.undo,
    ),
    "timeline.redo": _write(
        _schema(sequence_id=STRING),
        timeline.redo,
    ),
    "subtitle.list": _read(
        _schema(sequence_id=STRING),
        language_audio.list_subtitles,
    ),
    "subtitle.segment.update": _write(
        _schema(
            [
                "document_id",
                "segment_id",
                "start_frame",
                "end_frame",
                "text",
            ],
            document_id=STRING,
            segment_id=STRING,
            start_frame=INTEGER,
            end_frame=INTEGER,
            text=STRING,
        ),
        language_audio.update_subtitle_segment,
    ),
    "transcript.get": _read(
        _schema(sequence_id=STRING, document_id=STRING),
        language_audio.get_transcript,
    ),
    "transcript.edit.preview": _read(
        _schema(["edit"], edit=TRANSCRIPT_EDIT_REQUEST),
        language_audio.preview_transcript_edit,
    ),
    "transcript.edit.apply": _write(
        _schema(
            ["plan"],
            plan=TRANSCRIPT_EDIT_PLAN,
            accept_warnings=BOOLEAN,
        ),
        language_audio.apply_transcript_edit,
    ),
    "audio.inspect": _read(
        _schema(sequence_id=STRING),
        language_audio.inspect_audio,
    ),
    "audio.bus.update": _write(
        _schema(["bus_id", "changes"], bus_id=STRING, changes=OBJECT),
        language_audio.update_audio_bus,
    ),
    "audio.effect.save": _write(
        _schema(["effect"], effect=OBJECT),
        language_audio.save_audio_effect,
    ),
    "audio.effect.remove": _write(
        _schema(["effect_id"], effect_id=STRING),
        language_audio.remove_audio_effect,
    ),
    "preview.render": _write(
        _schema(sequence_id=STRING, use_proxies=BOOLEAN),
        timeline.render_preview,
    ),
    "export.sequence": _write(
        _schema(
            ["output_path"],
            sequence_id=STRING,
            output_path=STRING,
            format={
                "enum": ["h264", "hevc", "av1", "prores", "audio"]
            },
            preset=OBJECT,
            overwrite=BOOLEAN,
            timeout=NUMBER,
        ),
        timeline.export_sequence,
        task_backed=True,
    ),
    "task.list": _read(_schema(), tasks.list_tasks),
    "task.status": _read(
        _schema(["task_id"], task_id=STRING),
        tasks.get_task,
    ),
    "task.start": _write(
        _schema(
            ["task_command"],
            task_command=OBJECT,
            sequence_id=STRING,
            input_asset_ids=ARRAY_OF_STRINGS,
            timeout=NUMBER,
        ),
        tasks.start_task,
        task_backed=True,
    ),
    "task.resume": _write(
        _schema(["task_id"], task_id=STRING, timeout=NUMBER),
        tasks.resume_task,
        task_backed=True,
    ),
    "web.import": _write(
        _schema(["source"], source=STRING),
        web.import_web,
    ),
    "web.inspect": _read(
        _schema(["asset_id"], asset_id=STRING),
        web.inspect_web,
    ),
    "web.clip.get": _read(
        _schema(["clip_id"], clip_id=STRING),
        web.get_web_clip,
    ),
    "web.clip.update": _write(
        _schema(
            ["sequence_id", "clip_id", "scene_id", "updates"],
            sequence_id=STRING,
            clip_id=STRING,
            updates=OBJECT,
            scene_id=STRING,
            expected_revision=INTEGER,
            actor={"enum": ["human", "automation"]},
        ),
        web.update_web_clip,
    ),
    "web.clip.diff": _read(
        _schema(
            ["sequence_id", "clip_id", "scene_id", "updates"],
            sequence_id=STRING,
            clip_id=STRING,
            updates=OBJECT,
            scene_id=STRING,
            expected_revision=INTEGER,
            actor={"enum": ["human", "automation"]},
        ),
        web.diff_web_clip,
    ),
    "web.clip.variant.select": _write(
        _schema(
            ["sequence_id", "clip_id", "variant_id"],
            sequence_id=STRING,
            clip_id=STRING,
            variant_id=STRING,
            expected_revision=INTEGER,
        ),
        web.select_variant,
    ),
    "web.clip.keyframe.set": _write(
        _schema(
            [
                "sequence_id",
                "clip_id",
                "scene_id",
                "layer_id",
                "field",
                "time_ms",
                "value",
            ],
            sequence_id=STRING,
            clip_id=STRING,
            scene_id=STRING,
            layer_id=STRING,
            field=STRING,
            time_ms=INTEGER,
            value=ANY,
            easing=OBJECT,
            expected_revision=INTEGER,
            actor={"enum": ["human", "automation"]},
        ),
        web.set_keyframe,
    ),
    "web.clip.keyframe.remove": _write(
        _schema(
            ["sequence_id", "clip_id", "scene_id", "layer_id", "field", "time_ms"],
            sequence_id=STRING,
            clip_id=STRING,
            scene_id=STRING,
            layer_id=STRING,
            field=STRING,
            time_ms=INTEGER,
            expected_revision=INTEGER,
        ),
        web.remove_keyframe,
    ),
    "web.clip.theme.update": _write(
        _schema(
            ["sequence_id", "clip_id", "changes"],
            sequence_id=STRING,
            clip_id=STRING,
            changes=OBJECT,
            expected_revision=INTEGER,
        ),
        web.update_theme,
    ),
    "web.clip.data.update": _write(
        _schema(
            ["sequence_id", "clip_id", "scene_id", "values"],
            sequence_id=STRING,
            clip_id=STRING,
            scene_id=STRING,
            values=OBJECT,
            source_kind={"enum": ["inline", "file", "api"]},
            source_label=STRING,
            expected_revision=INTEGER,
        ),
        web.update_data,
    ),
    "web.clip.data.snapshot": _write(
        _schema(
            ["sequence_id", "clip_id", "scene_id", "source"],
            sequence_id=STRING,
            clip_id=STRING,
            scene_id=STRING,
            source=STRING,
            field_id=STRING,
            expected_revision=INTEGER,
        ),
        web.snapshot_data,
    ),
    "web.clip.lock.update": _write(
        _schema(
            ["sequence_id", "clip_id", "scene_id", "layer_id", "fields", "locked"],
            sequence_id=STRING,
            clip_id=STRING,
            scene_id=STRING,
            layer_id=STRING,
            fields=ARRAY_OF_STRINGS,
            locked=BOOLEAN,
            expected_revision=INTEGER,
        ),
        web.update_locks,
    ),
    "web.clip.render": _write(
        _schema(
            ["sequence_id", "clip_id"],
            sequence_id=STRING,
            clip_id=STRING,
            timeout=NUMBER,
        ),
        web.render_web_clip,
        task_backed=True,
    ),
    "web.clip.export": _write(
        _schema(
            ["sequence_id", "clip_id", "output_path", "format"],
            sequence_id=STRING,
            clip_id=STRING,
            output_path=STRING,
            format={
                "enum": [
                    "png",
                    "gif",
                    "alpha_video",
                    "video",
                    "overlay",
                ]
            },
            time_ms=INTEGER,
            background=STRING,
            overwrite=BOOLEAN,
            timeout=NUMBER,
        ),
        web.export_web_clip,
        task_backed=True,
    ),
    "web.batch.create": _write(
        _schema(
            ["source_sequence_id", "clip_id", "bindings"],
            source_sequence_id=STRING,
            clip_id=STRING,
            records=ARRAY_OF_OBJECTS,
            source=STRING,
            bindings=OBJECT,
            name_template=STRING,
            actor={"enum": ["human", "automation"]},
        ),
        web.create_batch,
    ),
    "web.asset.rebind": _write(
        _schema(
            ["asset_id", "source"],
            asset_id=STRING,
            source=STRING,
            dry_run=BOOLEAN,
            allow_conflicts=BOOLEAN,
        ),
        web.rebind_asset,
    ),
}
