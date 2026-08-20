from __future__ import annotations

from dataclasses import is_dataclass
from enum import Enum
from types import MappingProxyType, ModuleType
from typing import Any

from pydantic import BaseModel

from mediaflow.application.events import TaskEvent
from mediaflow.application.workflow_stage_handlers import WorkflowUpdate
from mediaflow.domain import (
    asr,
    audio,
    collaboration,
    downloads,
    dubbing,
    editor_fields,
    enums,
    exports,
    frame_clock,
    highlights,
    portable_timeline,
    progress,
    project,
    project_records,
    reference_comparison,
    runtime,
    runtime_capabilities,
    sequence_audio,
    sequence_bounds,
    settings,
    speech,
    subtitle_file,
    subtitles,
    task_commands,
    tasks,
    timeline,
    transcript_edits,
    visual_effects,
    web_exports,
    web_manifest,
    web_manifest_primitives,
    web_media_sources,
    web_state,
    workflows,
)
from mediaflow.infrastructure.proxy_service import ProxyDecision
from mediaflow.project_presentation import RecentProjectSnapshot
from mediaflow.project_task_settlement import ProjectTaskResult


def _registered_module_types(
    registry: dict[str, type[Any]],
    reverse: dict[type[Any], str],
    namespace: str,
    module: ModuleType,
) -> None:
    for name, value in vars(module).items():
        if not isinstance(value, type) or value.__module__ != module.__name__:
            continue
        if not (
            issubclass(value, (BaseModel, Enum))
            or (is_dataclass(value) and value is not type)
        ):
            continue
        _register(registry, reverse, f"domain.{namespace}.{name}", value)


def _register(
    registry: dict[str, type[Any]],
    reverse: dict[type[Any], str],
    schema_id: str,
    value_type: type[Any],
) -> None:
    if schema_id in registry or value_type in reverse:
        raise RuntimeError(f"Duplicate Editor Service transport schema: {schema_id}")
    registry[schema_id] = value_type
    reverse[value_type] = schema_id


def _transport_types() -> tuple[dict[str, type[Any]], dict[type[Any], str]]:
    registry: dict[str, type[Any]] = {}
    reverse: dict[type[Any], str] = {}
    modules = {
        "asr": asr,
        "audio": audio,
        "collaboration": collaboration,
        "downloads": downloads,
        "dubbing": dubbing,
        "editor-fields": editor_fields,
        "enums": enums,
        "exports": exports,
        "frame-clock": frame_clock,
        "highlights": highlights,
        "portable-timeline": portable_timeline,
        "progress": progress,
        "project": project,
        "project-records": project_records,
        "reference-comparison": reference_comparison,
        "runtime": runtime,
        "runtime-capabilities": runtime_capabilities,
        "sequence-audio": sequence_audio,
        "sequence-bounds": sequence_bounds,
        "settings": settings,
        "speech": speech,
        "subtitle-file": subtitle_file,
        "subtitles": subtitles,
        "task-commands": task_commands,
        "tasks": tasks,
        "timeline": timeline,
        "transcript-edits": transcript_edits,
        "visual-effects": visual_effects,
        "web-exports": web_exports,
        "web-manifest": web_manifest,
        "web-manifest-primitives": web_manifest_primitives,
        "web-media-sources": web_media_sources,
        "web-state": web_state,
        "workflows": workflows,
    }
    for namespace, module in modules.items():
        _registered_module_types(registry, reverse, namespace, module)
    _register(registry, reverse, "application.task-event", TaskEvent)
    _register(registry, reverse, "application.workflow-update", WorkflowUpdate)
    _register(registry, reverse, "application.proxy-decision", ProxyDecision)
    _register(registry, reverse, "application.recent-project-snapshot", RecentProjectSnapshot)
    _register(registry, reverse, "application.project-task-result", ProjectTaskResult)
    return registry, reverse


_TRANSPORT_TYPES, _TRANSPORT_SCHEMA_IDS = _transport_types()
TRANSPORT_TYPES = MappingProxyType(_TRANSPORT_TYPES)


def transport_schema_id(value_type: type[Any]) -> str:
    try:
        return _TRANSPORT_SCHEMA_IDS[value_type]
    except KeyError as error:
        raise TypeError(
            f"Type is not registered for Editor Service transport: {value_type!r}"
        ) from error


def transport_schema_type(schema_id: str) -> type[Any]:
    try:
        return TRANSPORT_TYPES[schema_id]
    except KeyError as error:
        raise ValueError(
            f"Unknown Editor Service transport schema: {schema_id!r}"
        ) from error
