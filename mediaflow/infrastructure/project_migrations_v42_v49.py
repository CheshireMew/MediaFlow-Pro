from __future__ import annotations

import json
import sqlite3
from typing import Any

from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.timeline_history import (
    TIMELINE_HISTORY_MODE,
    compact_timeline_change,
)

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v42_to_v43(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=42,
            target_version=43,
            migrate_database=_migrate_v42_history_documents,
        )
        _migrate_v42_history_documents(connection)
        connection.execute(
            "UPDATE schema_info SET version=43 WHERE component='project'"
        )


def _migrate_v42_history_documents(connection: sqlite3.Connection) -> None:
    _migrate_command_column(connection, "undo_group", "command_json")
    _migrate_command_column(
        connection,
        "project_event",
        "inverse_command_json",
        nullable=True,
    )


def _migrate_command_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    *,
    nullable: bool = False,
) -> None:
    predicate = f' WHERE "{column}" IS NOT NULL' if nullable else ""
    rows = connection.execute(
        f'SELECT rowid, "{column}" FROM "{table}"{predicate}'
    ).fetchall()
    for row in rows:
        document = json.loads(str(row[1]))
        migrated, changed = _compact_timeline_actions(document)
        if changed:
            connection.execute(
                f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',
                (
                    json.dumps(
                        migrated,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    row[0],
                ),
            )


def _compact_timeline_actions(value: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, dict):
        raise ValueError("Persisted project edit command must be an object")
    migrated = dict(value)
    changed = False
    for key in ("undo_actions", "redo_actions"):
        actions = migrated.get(key)
        if not isinstance(actions, list):
            raise ValueError(f"Persisted project edit command {key} must be an array")
        migrated_actions: list[Any] = []
        for raw_action in actions:
            action, action_changed = _compact_timeline_action(raw_action)
            migrated_actions.append(action)
            changed = changed or action_changed
        migrated[key] = migrated_actions
    return migrated, changed


def _compact_timeline_action(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, dict):
        raise ValueError("Persisted project edit action must be an object")
    kind = value.get("kind")
    if not isinstance(kind, str) or not kind.startswith("timeline.restore:"):
        return value, False
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Persisted timeline edit action payload must be an object")
    mode = payload.get("mode")
    if mode == "frame_clock":
        return value, False
    if mode not in {"state", TIMELINE_HISTORY_MODE}:
        raise ValueError(f"Unknown persisted timeline edit action mode: {mode!r}")
    source = TimelineState.model_validate(payload.get("source"))
    destination = TimelineState.model_validate(payload.get("destination"))
    source_patch, destination_patch = compact_timeline_change(
        source,
        destination,
    )
    migrated_payload = dict(payload)
    migrated_payload.update(
        {
            "mode": TIMELINE_HISTORY_MODE,
            "source": source_patch.model_dump(
                mode="json",
                exclude_computed_fields=True,
            ),
            "destination": destination_patch.model_dump(
                mode="json",
                exclude_computed_fields=True,
            ),
        }
    )
    migrated_action = dict(value)
    migrated_action["payload"] = migrated_payload
    return migrated_action, migrated_action != value
