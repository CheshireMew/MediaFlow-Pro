from __future__ import annotations

import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v57_to_v58(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=57,
            target_version=58,
            migrate_database=_add_execution_scope,
        )
        _add_execution_scope(connection)
        connection.execute("UPDATE schema_info SET version=58 WHERE component='project'")


def _add_execution_scope(connection: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(task)")}
    if "execution_scope" not in columns:
        connection.execute(
            "ALTER TABLE task ADD COLUMN execution_scope TEXT NOT NULL DEFAULT 'project' "
            "CHECK(execution_scope IN ('project', 'archive_history'))"
        )
