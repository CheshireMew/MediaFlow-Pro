from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.timeline_history import (
    TIMELINE_HISTORY_MODE,
    compact_timeline_change,
)

from .editable_media_project_migration import migrate_project_editable_media_to_v6
from .project_snapshot_migration import migrate_version_snapshots

_PRE_COLLABORATION_IDEMPOTENCY_PREFIX = "pre-v44:"


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
        connection.execute("UPDATE schema_info SET version=43 WHERE component='project'")


def migrate_v43_to_v44(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=43,
            target_version=44,
            migrate_database=_begin_collaboration_idempotency_epoch,
        )
        _begin_collaboration_idempotency_epoch(connection)
        connection.execute("UPDATE schema_info SET version=44 WHERE component='project'")


def migrate_v44_to_v45(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=44,
            target_version=45,
            migrate_database=_add_native_freeze_and_subtitle_track_style,
        )
        _add_native_freeze_and_subtitle_track_style(connection)
        connection.execute("UPDATE schema_info SET version=45 WHERE component='project'")


def migrate_v45_to_v46(
    workspace,
    *,
    chromium: Path | None,
) -> None:
    with workspace.transaction() as connection:
        migrate_project_editable_media_to_v6(
            workspace,
            chromium=chromium,
            target_project_schema_version=46,
        )
        web_assets = {
            str(row["asset_id"]): (
                str(row["path"]),
                str(row["manifest_json"]),
                str(row["source_hash"]),
            )
            for row in connection.execute(
                """SELECT web_asset.asset_id, asset.path,
                          web_asset.manifest_json, web_asset.source_hash
                   FROM web_asset
                   JOIN asset ON asset.id=web_asset.asset_id"""
            ).fetchall()
        }
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=45,
            target_version=46,
            migrate_database=lambda snapshot: _migrate_v45_web_snapshot(
                snapshot,
                web_assets,
            ),
        )


def _migrate_v45_web_snapshot(
    connection: sqlite3.Connection,
    web_assets: dict[str, tuple[str, str, str]],
) -> None:
    for asset_id, (path, manifest_json, source_hash) in web_assets.items():
        if connection.execute(
            "SELECT 1 FROM web_asset WHERE asset_id=?",
            (asset_id,),
        ).fetchone() is None:
            continue
        connection.execute("UPDATE asset SET path=? WHERE id=?", (path, asset_id))
        connection.execute(
            "UPDATE web_asset SET manifest_json=?, source_hash=? WHERE asset_id=?",
            (manifest_json, source_hash, asset_id),
        )
        rows = connection.execute(
            """SELECT state.clip_id, state.state_json
               FROM web_clip_state AS state
               JOIN clip ON clip.id=state.clip_id
               WHERE clip.asset_id=?""",
            (asset_id,),
        ).fetchall()
        for row in rows:
            state = json.loads(str(row["state_json"]))
            if not isinstance(state, dict):
                raise ValueError("Project version web state must be an object")
            state["source_hash"] = source_hash
            connection.execute(
                "UPDATE web_clip_state SET state_json=? WHERE clip_id=?",
                (
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                    row["clip_id"],
                ),
            )


def _add_native_freeze_and_subtitle_track_style(
    connection: sqlite3.Connection,
) -> None:
    track_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(track)").fetchall()}
    if "subtitle_style_json" not in track_columns:
        connection.execute("ALTER TABLE track ADD COLUMN subtitle_style_json TEXT")
    clip_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(clip)").fetchall()}
    if "freeze_source_frame" not in clip_columns:
        connection.execute("ALTER TABLE clip ADD COLUMN freeze_source_frame INTEGER")


def _begin_collaboration_idempotency_epoch(
    connection: sqlite3.Connection,
) -> None:
    # Collaboration v3 binds idempotency to actor, revision, write set, and
    # undo group. Earlier hashes only covered arguments, so their keys must
    # leave the active namespace instead of causing false reuse conflicts.
    connection.execute(
        "UPDATE automation_request SET request_id=? || request_id",
        (_PRE_COLLABORATION_IDEMPOTENCY_PREFIX,),
    )
    connection.execute(
        """UPDATE task
           SET idempotency_key=? || idempotency_key
           WHERE idempotency_key IS NOT NULL""",
        (_PRE_COLLABORATION_IDEMPOTENCY_PREFIX,),
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
    rows = connection.execute(f'SELECT rowid, "{column}" FROM "{table}"{predicate}').fetchall()
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
