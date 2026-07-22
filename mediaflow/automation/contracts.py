from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from mediaflow.domain.model_base import DomainModel

AUTOMATION_PROTOCOL: Literal["mediaflow-cli"] = "mediaflow-cli"
AUTOMATION_VERSION: Literal[1] = 1


class AutomationRequest(DomainModel):
    protocol: Literal["mediaflow-cli"] = AUTOMATION_PROTOCOL
    version: Literal[1] = AUTOMATION_VERSION
    operation: str
    project: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


def _schema(required: Sequence[str] = (), **properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(required),
        "properties": properties,
    }


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
NUMBER = {"type": "number"}
BOOLEAN = {"type": "boolean"}
OBJECT = {"type": "object"}
ARRAY_OF_STRINGS = {"type": "array", "items": STRING}
ARRAY_OF_OBJECTS = {"type": "array", "items": OBJECT}
ANY: dict[str, Any] = {}


OPERATION_SCHEMAS: dict[str, dict[str, Any]] = {
    "project.create": _schema(["name"], name=STRING),
    "project.inspect": _schema(),
    "asset.list": _schema(),
    "asset.import": _schema(["source"], source=STRING, timeout=NUMBER),
    "sequence.short.create": _schema(
        ["source_sequence_id", "start_frame", "end_frame"],
        source_sequence_id=STRING,
        start_frame=INTEGER,
        end_frame=INTEGER,
        name=STRING,
    ),
    "timeline.get": _schema(sequence_id=STRING),
    "timeline.track.add": _schema(
        ["kind"],
        sequence_id=STRING,
        kind={"enum": ["video", "audio", "subtitle"]},
        name=STRING,
    ),
    "timeline.clip.add": _schema(
        ["track_id", "asset_id", "timeline_start", "source_in", "duration"],
        sequence_id=STRING,
        track_id=STRING,
        asset_id=STRING,
        timeline_start=INTEGER,
        source_in=INTEGER,
        duration=INTEGER,
        speed_numerator=INTEGER,
        speed_denominator=INTEGER,
    ),
    "timeline.clip.move": _schema(
        ["clip_id", "timeline_start"],
        sequence_id=STRING,
        clip_id=STRING,
        timeline_start=INTEGER,
        track_id=STRING,
    ),
    "timeline.clip.copy": _schema(
        ["clip_id", "timeline_start"],
        sequence_id=STRING,
        clip_id=STRING,
        timeline_start=INTEGER,
        track_id=STRING,
    ),
    "timeline.clip.split": _schema(
        ["clip_id", "split_frame"], sequence_id=STRING, clip_id=STRING, split_frame=INTEGER
    ),
    "timeline.clip.delete": _schema(
        ["clip_ids"], sequence_id=STRING, clip_ids=ARRAY_OF_STRINGS, ripple=BOOLEAN
    ),
    "timeline.clip.transform": _schema(
        ["clip_id", "transform"], sequence_id=STRING, clip_id=STRING, transform=OBJECT
    ),
    "timeline.clip.audio": _schema(
        ["clip_id", "audio"], sequence_id=STRING, clip_id=STRING, audio=OBJECT
    ),
    "timeline.undo": _schema(sequence_id=STRING),
    "timeline.redo": _schema(sequence_id=STRING),
    "subtitle.list": _schema(sequence_id=STRING),
    "subtitle.segment.update": _schema(
        ["document_id", "segment_id", "start_frame", "end_frame", "text"],
        document_id=STRING,
        segment_id=STRING,
        start_frame=INTEGER,
        end_frame=INTEGER,
        text=STRING,
    ),
    "audio.inspect": _schema(sequence_id=STRING),
    "audio.bus.update": _schema(["bus_id", "changes"], bus_id=STRING, changes=OBJECT),
    "audio.effect.save": _schema(["effect"], effect=OBJECT),
    "audio.effect.remove": _schema(["effect_id"], effect_id=STRING),
    "preview.render": _schema(sequence_id=STRING, use_proxies=BOOLEAN),
    "export.sequence": _schema(
        ["output_path"],
        sequence_id=STRING,
        output_path=STRING,
        format={"enum": ["h264", "hevc", "av1", "prores", "audio"]},
        preset=OBJECT,
        timeout=NUMBER,
    ),
    "task.list": _schema(),
    "task.status": _schema(["task_id"], task_id=STRING),
    "task.start": _schema(
        ["task_command"],
        task_command=OBJECT,
        sequence_id=STRING,
        input_asset_ids=ARRAY_OF_STRINGS,
        timeout=NUMBER,
    ),
    "task.resume": _schema(["task_id"], task_id=STRING, timeout=NUMBER),
    "web.import": _schema(["source"], source=STRING),
    "web.inspect": _schema(["asset_id"], asset_id=STRING),
    "web.clip.get": _schema(["clip_id"], clip_id=STRING),
    "web.clip.update": _schema(
        ["sequence_id", "clip_id", "updates"],
        sequence_id=STRING,
        clip_id=STRING,
        updates=OBJECT,
        expected_revision=INTEGER,
        actor={"enum": ["human", "automation"]},
        layout_id=STRING,
    ),
    "web.clip.diff": _schema(
        ["sequence_id", "clip_id", "updates"],
        sequence_id=STRING,
        clip_id=STRING,
        updates=OBJECT,
        expected_revision=INTEGER,
        actor={"enum": ["human", "automation"]},
        layout_id=STRING,
    ),
    "web.clip.layout.select": _schema(
        ["sequence_id", "clip_id", "layout_id"],
        sequence_id=STRING,
        clip_id=STRING,
        layout_id=STRING,
        expected_revision=INTEGER,
    ),
    "web.clip.keyframe.set": _schema(
        ["sequence_id", "clip_id", "layer_id", "field", "time_ms", "value"],
        sequence_id=STRING,
        clip_id=STRING,
        layer_id=STRING,
        field=STRING,
        time_ms=INTEGER,
        value=ANY,
        easing=OBJECT,
        expected_revision=INTEGER,
        actor={"enum": ["human", "automation"]},
    ),
    "web.clip.keyframe.remove": _schema(
        ["sequence_id", "clip_id", "layer_id", "field", "time_ms"],
        sequence_id=STRING,
        clip_id=STRING,
        layer_id=STRING,
        field=STRING,
        time_ms=INTEGER,
        expected_revision=INTEGER,
    ),
    "web.clip.theme.update": _schema(
        ["sequence_id", "clip_id", "changes"],
        sequence_id=STRING,
        clip_id=STRING,
        changes=OBJECT,
        expected_revision=INTEGER,
    ),
    "web.clip.data.update": _schema(
        ["sequence_id", "clip_id", "values"],
        sequence_id=STRING,
        clip_id=STRING,
        values=OBJECT,
        source_kind={"enum": ["inline", "file", "api"]},
        source_label=STRING,
        expected_revision=INTEGER,
    ),
    "web.clip.data.snapshot": _schema(
        ["sequence_id", "clip_id", "source"],
        sequence_id=STRING,
        clip_id=STRING,
        source=STRING,
        field_id=STRING,
        expected_revision=INTEGER,
    ),
    "web.clip.lock.update": _schema(
        ["sequence_id", "clip_id", "layer_id", "fields", "locked"],
        sequence_id=STRING,
        clip_id=STRING,
        layer_id=STRING,
        fields=ARRAY_OF_STRINGS,
        locked=BOOLEAN,
        expected_revision=INTEGER,
    ),
    "web.clip.render": _schema(
        ["sequence_id", "clip_id"],
        sequence_id=STRING,
        clip_id=STRING,
        timeout=NUMBER,
    ),
    "web.clip.export": _schema(
        ["sequence_id", "clip_id", "output_path", "format"],
        sequence_id=STRING,
        clip_id=STRING,
        output_path=STRING,
        format={"enum": ["png", "gif", "alpha_video", "video", "overlay"]},
        time_ms=INTEGER,
        background=STRING,
        overwrite=BOOLEAN,
    ),
    "web.batch.create": _schema(
        ["source_sequence_id", "clip_id", "bindings"],
        source_sequence_id=STRING,
        clip_id=STRING,
        records=ARRAY_OF_OBJECTS,
        source=STRING,
        bindings=OBJECT,
        name_template=STRING,
        actor={"enum": ["human", "automation"]},
    ),
    "web.asset.rebind": _schema(
        ["asset_id", "source"],
        asset_id=STRING,
        source=STRING,
        dry_run=BOOLEAN,
        allow_conflicts=BOOLEAN,
    ),
    "web.component.list": _schema(),
    "web.component.install": _schema(["source"], source=STRING),
    "web.component.import": _schema(
        ["component_id"], component_id=STRING, version_hash=STRING
    ),
}


MUTATING_OPERATIONS = {
    name
    for name in OPERATION_SCHEMAS
    if name
    not in {
        "project.inspect",
        "asset.list",
        "timeline.get",
        "subtitle.list",
        "audio.inspect",
        "task.list",
        "task.status",
        "web.inspect",
        "web.clip.get",
        "web.clip.diff",
        "web.component.list",
    }
}


def describe_contract() -> dict[str, Any]:
    return {
        "protocol": AUTOMATION_PROTOCOL,
        "version": AUTOMATION_VERSION,
        "transport": {
            "lifecycle": "short-process",
            "input": "single JSON object from a file or stdin",
            "output": "single JSON object on stdout",
        },
        "features": {
            "editable_web_media": True,
            "cooperative_desktop_updates": True,
            "remote_web_pages": False,
            "persistent_service": False,
            "web_keyframes": True,
            "web_brand_themes": True,
            "web_responsive_layouts": True,
            "web_data_snapshots": True,
            "web_batch_variants": True,
            "web_component_library": True,
            "web_field_locks_and_diff": True,
            "web_template_rebinding": True,
            "web_multi_format_export": True,
        },
        "operations": [
            {
                "name": name,
                "mutates_project": name in MUTATING_OPERATIONS,
                "arguments_schema": schema,
            }
            for name, schema in OPERATION_SCHEMAS.items()
        ],
    }


def validate_arguments(operation: str, arguments: dict[str, Any]) -> None:
    schema = OPERATION_SCHEMAS[operation]
    properties = schema["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError(f"Unknown arguments for {operation}: {sorted(unknown)}")
    missing = [name for name in schema["required"] if name not in arguments]
    if missing:
        raise ValueError(f"Missing arguments for {operation}: {missing}")
    expected_types: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for name, value in arguments.items():
        rule = properties[name]
        expected = rule.get("type")
        if expected and (
            not isinstance(value, expected_types[expected])
            or (expected in {"integer", "number"} and isinstance(value, bool))
        ):
            raise ValueError(f"arguments.{name} must be {expected}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"arguments.{name} must be one of {rule['enum']}")
        item_rule = rule.get("items")
        if item_rule and isinstance(value, list) and any(
            not isinstance(item, expected_types[item_rule["type"]]) for item in value
        ):
            raise ValueError(f"arguments.{name} contains an invalid item")
