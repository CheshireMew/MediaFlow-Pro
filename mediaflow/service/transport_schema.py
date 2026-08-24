from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import is_dataclass
from enum import Enum
from functools import cache
from importlib import import_module
from types import ModuleType
from typing import Any, cast

from pydantic import BaseModel

_DOMAIN_MODULES = {
    "asr": "mediaflow.domain.asr",
    "audio": "mediaflow.domain.audio",
    "collaboration": "mediaflow.domain.collaboration",
    "downloads": "mediaflow.domain.downloads",
    "dubbing": "mediaflow.domain.dubbing",
    "editor-fields": "mediaflow.domain.editor_fields",
    "enums": "mediaflow.domain.enums",
    "exports": "mediaflow.domain.exports",
    "frame-clock": "mediaflow.domain.frame_clock",
    "highlights": "mediaflow.domain.highlights",
    "portable-timeline": "mediaflow.domain.portable_timeline",
    "progress": "mediaflow.domain.progress",
    "project": "mediaflow.domain.project",
    "project-records": "mediaflow.domain.project_records",
    "reference-comparison": "mediaflow.domain.reference_comparison",
    "runtime": "mediaflow.domain.runtime",
    "runtime-capabilities": "mediaflow.domain.runtime_capabilities",
    "sequence-audio": "mediaflow.domain.sequence_audio",
    "sequence-bounds": "mediaflow.domain.sequence_bounds",
    "settings": "mediaflow.domain.settings",
    "speech": "mediaflow.domain.speech",
    "subtitle-file": "mediaflow.domain.subtitle_file",
    "subtitles": "mediaflow.domain.subtitles",
    "task-commands": "mediaflow.domain.task_commands",
    "tasks": "mediaflow.domain.tasks",
    "timeline": "mediaflow.domain.timeline",
    "transcript-edits": "mediaflow.domain.transcript_edits",
    "translation": "mediaflow.domain.translation",
    "visual-effects": "mediaflow.domain.visual_effects",
    "web-exports": "mediaflow.domain.web_exports",
    "web-manifest": "mediaflow.domain.web_manifest",
    "web-manifest-primitives": "mediaflow.domain.web_manifest_primitives",
    "web-media-sources": "mediaflow.domain.web_media_sources",
    "web-state": "mediaflow.domain.web_state",
    "workflows": "mediaflow.domain.workflows",
}
_DOMAIN_NAMESPACES = {module_name: namespace for namespace, module_name in _DOMAIN_MODULES.items()}
_SPECIAL_TYPES = {
    "application.task-event": ("mediaflow.application.events", "TaskEvent"),
    "application.workflow-update": (
        "mediaflow.application.workflow_models",
        "WorkflowUpdate",
    ),
    "application.proxy-decision": (
        "mediaflow.infrastructure.proxy_service",
        "ProxyDecision",
    ),
    "application.recent-project-snapshot": (
        "mediaflow.application.presentation_models",
        "RecentProjectSnapshot",
    ),
    "application.project-task-result": (
        "mediaflow.project_task_settlement",
        "ProjectTaskResult",
    ),
}
_SPECIAL_SCHEMA_IDS = {
    reference: schema_id for schema_id, reference in _SPECIAL_TYPES.items()
}


def _is_transport_type(value: object) -> bool:
    return isinstance(value, type) and (
        issubclass(value, (BaseModel, Enum))
        or (is_dataclass(value) and value is not type)
    )


def _require_module_type(module: ModuleType, name: str, schema_id: str) -> type[Any]:
    value = getattr(module, name, None)
    if not _is_transport_type(value) or value.__module__ != module.__name__:
        raise ValueError(f"Unknown Editor Service transport schema: {schema_id!r}")
    return cast(type[Any], value)


@cache
def transport_schema_id(value_type: type[Any]) -> str:
    special = _SPECIAL_SCHEMA_IDS.get((value_type.__module__, value_type.__name__))
    if special is not None:
        return special
    namespace = _DOMAIN_NAMESPACES.get(value_type.__module__)
    if namespace is None or not _is_transport_type(value_type):
        raise TypeError(
            f"Type is not registered for Editor Service transport: {value_type!r}"
        )
    module = import_module(value_type.__module__)
    if getattr(module, value_type.__name__, None) is not value_type:
        raise TypeError(
            f"Type is not registered for Editor Service transport: {value_type!r}"
        )
    return f"domain.{namespace}.{value_type.__name__}"


@cache
def transport_schema_type(schema_id: str) -> type[Any]:
    special = _SPECIAL_TYPES.get(schema_id)
    if special is not None:
        module_name, name = special
        return _require_module_type(import_module(module_name), name, schema_id)
    if not schema_id.startswith("domain."):
        raise ValueError(f"Unknown Editor Service transport schema: {schema_id!r}")
    qualified_name = schema_id[len("domain.") :]
    namespace, separator, name = qualified_name.rpartition(".")
    domain_module_name = _DOMAIN_MODULES.get(namespace)
    if not separator or not name or domain_module_name is None:
        raise ValueError(f"Unknown Editor Service transport schema: {schema_id!r}")
    return _require_module_type(import_module(domain_module_name), name, schema_id)


def _all_transport_types() -> dict[str, type[Any]]:
    registry: dict[str, type[Any]] = {}
    for namespace, module_name in _DOMAIN_MODULES.items():
        module = import_module(module_name)
        for name, value in vars(module).items():
            if not _is_transport_type(value) or value.__module__ != module.__name__:
                continue
            registry[f"domain.{namespace}.{name}"] = value
    for schema_id in _SPECIAL_TYPES:
        registry[schema_id] = transport_schema_type(schema_id)
    if len(registry) != len(set(registry.values())):
        raise RuntimeError("Duplicate Editor Service transport type registration")
    return registry


class _LazyTransportTypes(Mapping[str, type[Any]]):
    """Read-only compatibility view that expands only when it is enumerated."""

    def __getitem__(self, schema_id: str) -> type[Any]:
        return transport_schema_type(schema_id)

    def __iter__(self) -> Iterator[str]:
        return iter(_all_transport_types())

    def __len__(self) -> int:
        return len(_all_transport_types())


TRANSPORT_TYPES: Mapping[str, type[Any]] = _LazyTransportTypes()
