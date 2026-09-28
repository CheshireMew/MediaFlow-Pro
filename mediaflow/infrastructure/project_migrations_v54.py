from __future__ import annotations

import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v53_to_v54(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=53,
            target_version=54,
            migrate_database=_add_project_collections,
        )
        _add_project_collections(connection)
        connection.execute(
            "UPDATE schema_info SET version=54 WHERE component='project'"
        )


def _add_project_collections(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS project_collection (
            id TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            collection_json TEXT NOT NULL
        )
        """
    )
