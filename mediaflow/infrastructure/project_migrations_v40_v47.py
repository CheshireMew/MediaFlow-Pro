from __future__ import annotations

import json
import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v40_to_v41(workspace) -> None:
    with workspace.transaction() as connection:
        _migrate_v40_database(connection)


def _migrate_v40_database(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(project_event)").fetchall()
    }
    if "inverse_command_json" not in columns:
        connection.execute(
            "ALTER TABLE project_event ADD COLUMN inverse_command_json TEXT"
        )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS undo_group (
               id TEXT NOT NULL,
               project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
               source_revision INTEGER NOT NULL,
               state_revision INTEGER NOT NULL,
               label TEXT NOT NULL,
               actor_json TEXT NOT NULL,
               write_set_json TEXT NOT NULL,
               command_json TEXT NOT NULL,
               state TEXT NOT NULL
                   CHECK(state IN ('applied', 'undone', 'discarded')),
               created_at INTEGER NOT NULL,
               updated_at INTEGER NOT NULL,
               PRIMARY KEY(project_id, id)
           )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_undo_group_project_state_revision
           ON undo_group(project_id, state, state_revision)"""
    )
    connection.execute(
        "UPDATE schema_info SET version=41 WHERE component='project'"
    )


def migrate_v41_to_v42(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=(40, 41),
            target_version=42,
            migrate_database=_migrate_v41_snapshot_database,
        )
        _migrate_interim_encoder_policy_documents(connection)
        connection.execute(
            "UPDATE schema_info SET version=42 WHERE component='project'"
        )


def _migrate_v41_snapshot_database(connection: sqlite3.Connection) -> None:
    version = int(
        connection.execute(
            "SELECT version FROM schema_info WHERE component='project'"
        ).fetchone()[0]
    )
    if version == 40:
        _migrate_v40_database(connection)
    _migrate_interim_encoder_policy_documents(connection)


def _migrate_interim_encoder_policy_documents(
    connection: sqlite3.Connection,
) -> None:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    for table in tables:
        columns = [
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            if str(row[1]).endswith("_json")
        ]
        for column in columns:
            rows = connection.execute(
                f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
            ).fetchall()
            for row in rows:
                document = json.loads(str(row[1]))
                migrated, changed = _migrate_interim_encoder_policy(document)
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


def _migrate_interim_encoder_policy(value):
    if isinstance(value, list):
        items = []
        changed = False
        for item in value:
            migrated, item_changed = _migrate_interim_encoder_policy(item)
            items.append(migrated)
            changed = changed or item_changed
        return items, changed
    if not isinstance(value, dict):
        return value, False
    migrated = {}
    changed = False
    for key, item in value.items():
        migrated_item, item_changed = _migrate_interim_encoder_policy(item)
        migrated[key] = migrated_item
        changed = changed or item_changed
    legacy_field = "video_encoder"
    if legacy_field not in migrated:
        return migrated, changed
    if "encoder_policy" in migrated:
        raise ValueError("Export preset contains both legacy and current encoder policy fields")
    policy = migrated.pop(legacy_field)
    migrated["encoder_policy"] = _portable_interim_encoder_policy(policy)
    return migrated, True


def _portable_interim_encoder_policy(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Interim video encoder policy must be an object")
    mode = value.get("mode")
    backend = value.get("backend")
    unexpected = set(value) - {"mode", "backend"}
    if unexpected:
        raise ValueError(
            "Interim video encoder policy has unknown fields: "
            + ", ".join(sorted(unexpected))
        )
    if mode == "software":
        if backend is not None:
            raise ValueError("Software encoder policy cannot select a hardware backend")
        return {"mode": "software", "vendor": "auto"}
    if mode == "hardware_required":
        raise ValueError(
            "Interim hardware_required policy cannot be represented by the portable contract"
        )
    if mode != "hardware_preferred":
        raise ValueError(f"Unknown interim video encoder policy mode: {mode!r}")
    vendors = {
        None: "auto",
        "nvenc": "nvidia",
        "qsv": "intel",
        "amf": "amd",
        "videotoolbox": "apple",
        "vaapi": "auto",
    }
    if backend not in vendors:
        raise ValueError(f"Unknown interim video encoder backend: {backend!r}")
    return {"mode": "prefer_hardware", "vendor": vendors[backend]}
