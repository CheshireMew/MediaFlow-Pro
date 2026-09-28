from __future__ import annotations

import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v54_to_v55(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=54,
            target_version=55,
            migrate_database=_add_multicam_groups,
        )
        _add_multicam_groups(connection)
        connection.execute(
            "UPDATE schema_info SET version=55 WHERE component='project'"
        )


def _add_multicam_groups(connection: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(sequence)")}
    if "multicam_groups_json" in columns:
        return
    connection.execute(
        "ALTER TABLE sequence ADD COLUMN multicam_groups_json TEXT NOT NULL DEFAULT '[]'"
    )
