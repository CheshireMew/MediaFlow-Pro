from __future__ import annotations

import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v51_to_v52(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=51,
            target_version=52,
            migrate_database=_add_review_threads,
        )
        _add_review_threads(connection)
        connection.execute(
            "UPDATE schema_info SET version=52 WHERE component='project'"
        )


def _add_review_threads(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS review_thread (
            id TEXT PRIMARY KEY,
            sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
            start_frame INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open', 'resolved', 'archived')),
            thread_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_review_thread_sequence_status_time
        ON review_thread(sequence_id, status, start_frame)
        """
    )
