from __future__ import annotations

from typing import Any

from mediaflow.service.client import EditorServiceRpcError


def collaboration_conflict_details(error: EditorServiceRpcError) -> dict[str, Any]:
    details = dict(error.data) if isinstance(error.data, dict) else {}
    events = details.get("conflicting_events")
    actors: list[str] = []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("actor"), dict):
                continue
            actor = event["actor"]
            label = str(actor.get("name") or actor.get("id") or actor.get("kind") or "")
            if label and label not in actors:
                actors.append(label)
    details.update(
        {
            "message": str(error),
            "actors": actors,
            "paths": list(details.get("write_set") or []),
        }
    )
    return details


def snap_tolerance_frames(pixels_per_frame: float) -> int:
    return max(1, round(8.0 / max(0.01, pixels_per_frame)))


def updated_selection(
    current_ids: list[str],
    item_id: str,
    *,
    toggle: bool,
) -> list[str]:
    if not item_id:
        return []
    if not toggle:
        return [item_id]
    if item_id in current_ids:
        return [value for value in current_ids if value != item_id]
    return [*current_ids, item_id]
