from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .editable_media_project_migration import (
    migrate_project_editable_media_to_v5,
)
from .project_snapshot_migration import migrate_version_snapshots


def migrate_v33_to_v34(workspace) -> None:
    with workspace.transaction() as connection:
        automation_columns = {
            item["name"]
            for item in connection.execute(
                "PRAGMA table_info(automation_request)"
            ).fetchall()
        }
        if "state" not in automation_columns:
            connection.execute(
                """ALTER TABLE automation_request
                   ADD COLUMN state TEXT NOT NULL DEFAULT 'completed'"""
            )

        task_columns = {
            item["name"]
            for item in connection.execute("PRAGMA table_info(task)").fetchall()
        }
        for column, definition in (
            ("execution_owner_id", "TEXT"),
            ("heartbeat_at", "INTEGER"),
            ("lease_expires_at", "INTEGER"),
            ("stop_request", "TEXT"),
        ):
            if column not in task_columns:
                connection.execute(
                    f"ALTER TABLE task ADD COLUMN {column} {definition}"
                )

        running_rows = connection.execute(
            "SELECT id FROM task WHERE status='running'"
        ).fetchall()
        for row in running_rows:
            owner_id = f"expired:migration:v34:{row['id']}"
            connection.execute(
                """UPDATE task
                   SET execution_owner_id=?, heartbeat_at=0, lease_expires_at=1,
                       stop_request=NULL
                   WHERE id=?""",
                (owner_id, row["id"]),
            )

        for row in connection.execute(
            """SELECT cursor, task_id, payload_json
               FROM task_event"""
        ).fetchall():
            payload = json.loads(str(row["payload_json"]))
            if payload.get("status") != "running":
                continue
            payload["execution_owner_id"] = (
                f"expired:migration:v34:{row['task_id']}"
            )
            payload["heartbeat_at"] = 0
            payload["lease_expires_at"] = 1
            payload["stop_request"] = None
            connection.execute(
                "UPDATE task_event SET payload_json=? WHERE cursor=?",
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    row["cursor"],
                ),
            )

        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (34,),
        )


def migrate_v34_to_v35(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS task_consumption (
                   task_id TEXT PRIMARY KEY
                       REFERENCES task(id) ON DELETE CASCADE,
                   project_id TEXT NOT NULL
                       REFERENCES project(id) ON DELETE CASCADE,
                   task_revision INTEGER NOT NULL,
                   result_json TEXT NOT NULL,
                   created_at INTEGER NOT NULL
               )"""
        )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (35,),
        )


def migrate_v35_to_v36(
    workspace,
    *,
    chromium: Path | None,
) -> None:
    with workspace.transaction():
        migrate_project_editable_media_to_v5(
            workspace,
            chromium=chromium,
        )


def migrate_v36_to_v37(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS asset_bin (
                   id TEXT PRIMARY KEY,
                   project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                   name TEXT NOT NULL,
                   parent_id TEXT REFERENCES asset_bin(id) ON DELETE CASCADE,
                   position INTEGER NOT NULL
               )"""
        )
        asset_columns = {
            item["name"]
            for item in connection.execute("PRAGMA table_info(asset)").fetchall()
        }
        if "bin_id" not in asset_columns:
            connection.execute(
                "ALTER TABLE asset ADD COLUMN bin_id TEXT REFERENCES asset_bin(id) ON DELETE SET NULL"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_asset_bin_project ON asset_bin(project_id, parent_id, position)"
        )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_bin_unique_name
               ON asset_bin(project_id, COALESCE(parent_id, ''), name COLLATE NOCASE)"""
        )
        connection.execute(
            "UPDATE schema_info SET version=37 WHERE component='project'"
        )


def migrate_v37_to_v38(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute("DROP INDEX IF EXISTS idx_asset_bin_unique_name")
        connection.execute(
            """CREATE UNIQUE INDEX idx_asset_bin_unique_name
               ON asset_bin(project_id, COALESCE(parent_id, ''), name COLLATE NOCASE)"""
        )
        clip_columns = {
            item["name"]
            for item in connection.execute("PRAGMA table_info(clip)").fetchall()
        }
        if "visual_effects_json" not in clip_columns:
            connection.execute(
                "ALTER TABLE clip ADD COLUMN visual_effects_json TEXT NOT NULL DEFAULT '[]'"
            )
        connection.execute(
            "UPDATE schema_info SET version=38 WHERE component='project'"
        )


def migrate_v38_to_v39(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS project_event (
                   cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                   project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                   base_revision INTEGER NOT NULL,
                   project_revision INTEGER NOT NULL,
                   operation TEXT NOT NULL,
                   actor_json TEXT NOT NULL,
                   request_id TEXT NOT NULL,
                   undo_group_id TEXT NOT NULL,
                   write_set_json TEXT NOT NULL,
                   changes_json TEXT NOT NULL,
                   result_json TEXT NOT NULL,
                   created_at INTEGER NOT NULL,
                   UNIQUE(project_id, project_revision)
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_project_event_project_cursor
               ON project_event(project_id, cursor)"""
        )
        connection.execute(
            "UPDATE schema_info SET version=39 WHERE component='project'"
        )


def migrate_v39_to_v40(workspace) -> None:
    with workspace.transaction() as connection:
        _migrate_v39_version_snapshots(workspace, connection)
        _migrate_v39_export_preset_documents(connection)
        connection.execute(
            "UPDATE schema_info SET version=40 WHERE component='project'"
        )


def _migrate_v39_export_preset_documents(connection: sqlite3.Connection) -> None:
    json_columns = (
        ("sequence_export_setting", "preset_json"),
        ("export_preset", "data_json"),
        ("export_history", "preset_json"),
        ("automation_request", "result_json"),
        ("project_event", "changes_json"),
        ("project_event", "result_json"),
        ("task", "command_json"),
        ("task_event", "payload_json"),
        ("task_consumption", "result_json"),
        ("workflow_run", "payload_json"),
    )
    for table, column in json_columns:
        for row in connection.execute(
            f"SELECT rowid, {column} AS document FROM {table}"
        ).fetchall():
            document = json.loads(str(row["document"]))
            migrated, changed = _migrate_export_presets(document)
            if changed:
                connection.execute(
                    f"UPDATE {table} SET {column}=? WHERE rowid=?",
                    (
                        json.dumps(
                            migrated,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        row["rowid"],
                    ),
                )


def _migrate_v39_version_snapshots(workspace, connection: sqlite3.Connection) -> None:
    migrate_version_snapshots(
        workspace,
        connection,
        source_version=39,
        target_version=40,
        migrate_database=_migrate_v39_export_preset_documents,
    )


def _migrate_export_presets(value):
    changed = False
    if isinstance(value, list):
        migrated_items = []
        for item in value:
            migrated, item_changed = _migrate_export_presets(item)
            migrated_items.append(migrated)
            changed = changed or item_changed
        return migrated_items, changed
    if not isinstance(value, dict):
        return value, False
    migrated_mapping = {}
    for key, item in value.items():
        migrated, item_changed = _migrate_export_presets(item)
        migrated_mapping[key] = migrated
        changed = changed or item_changed
    is_export_preset = (
        "video_codec" in migrated_mapping
        and "format" in migrated_mapping
        and "container" in migrated_mapping
        and "audio_codec" in migrated_mapping
        and "pixel_format" in migrated_mapping
    )
    if not is_export_preset:
        return migrated_mapping, changed
    codec = migrated_mapping.pop("video_codec")
    export_format = str(migrated_mapping.get("format") or "")
    migrated_mapping["encoder_policy"] = _portable_encoder_policy(
        codec,
        export_format,
    )
    return migrated_mapping, True


def _portable_encoder_policy(codec, export_format: str):
    if export_format == "audio":
        return None
    normalized = str(codec or "").strip().casefold()
    if normalized in {"libx264", "libx265", "libsvtav1", "prores_ks"}:
        return {"mode": "software", "vendor": "auto"}
    vendors = {
        "h264_nvenc": "nvidia",
        "hevc_nvenc": "nvidia",
        "av1_nvenc": "nvidia",
        "h264_qsv": "intel",
        "hevc_qsv": "intel",
        "av1_qsv": "intel",
        "h264_amf": "amd",
        "hevc_amf": "amd",
        "av1_amf": "amd",
        "h264_videotoolbox": "apple",
        "hevc_videotoolbox": "apple",
        "av1_videotoolbox": "apple",
        "h264_vaapi": "auto",
        "hevc_vaapi": "auto",
        "av1_vaapi": "auto",
    }
    vendor = vendors.get(normalized)
    if vendor is None:
        raise ValueError(f"Cannot migrate unknown video encoder: {codec!r}")
    return {"mode": "prefer_hardware", "vendor": vendor}
