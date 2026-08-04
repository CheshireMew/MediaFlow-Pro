from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.project_controller import ProjectSession
    from mediaflow.service.desktop_proxy import RemoteEditorProject


@dataclass(frozen=True)
class WebEditorContext:
    """Current editable-web selection shared by focused presentation facets."""

    clip_id: str
    asset_id: str
    manifest: dict
    persistent_state: dict
    runtime_state: dict
    edit_document: dict
    active_scene_id: str
    selected_layer_id: str


def require_mutable_web_clip(
    session: ProjectSession,
    clip_id: str,
) -> RemoteEditorProject:
    session._require_writable()
    if not clip_id:
        raise ValueError("请先选择网页片段")
    current = session.binding.current
    if current is None:
        raise RuntimeError("请先打开一个项目")
    return current


def find_web_descriptor(
    edit_document: dict,
    target: str,
    source_id: str,
) -> dict:
    try:
        return next(
            item
            for item in edit_document.get("descriptors", [])
            if item.get("target") == target and item.get("source_id") == source_id
        )
    except StopIteration as error:
        raise ValueError(f"网页编辑字段不存在：{target}/{source_id}") from error


def coerce_web_descriptor_value(descriptor: dict, value):
    kind = str(descriptor.get("kind") or "string")
    target = str(descriptor.get("target") or "")
    if target == "data" and isinstance(value, str):
        return json.loads(value)
    if kind == "number":
        return float(value)
    if kind == "integer":
        return int(round(float(value)))
    if kind == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    return value
