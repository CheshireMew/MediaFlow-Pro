from __future__ import annotations

import json
import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v50_to_v51(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=50,
            target_version=51,
            migrate_database=_add_native_masks,
        )
        _add_native_masks(connection)
        connection.execute(
            "UPDATE schema_info SET version=51 WHERE component='project'"
        )


def _add_native_masks(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(clip)").fetchall()
    }
    if "masks_json" not in columns:
        connection.execute(
            "ALTER TABLE clip ADD COLUMN masks_json TEXT NOT NULL DEFAULT '[]'"
        )
    rows = connection.execute("SELECT id, visual_effects_json FROM clip").fetchall()
    for row in rows:
        effects = json.loads(str(row["visual_effects_json"]))
        if not isinstance(effects, list):
            raise ValueError("Persisted visual effects must be an array")
        for effect in effects:
            if not isinstance(effect, dict):
                raise ValueError("Persisted visual effect must be an object")
            effect.setdefault("mask_id", None)
        connection.execute(
            "UPDATE clip SET visual_effects_json=? WHERE id=?",
            (
                json.dumps(effects, ensure_ascii=False, separators=(",", ":")),
                row["id"],
            ),
        )
