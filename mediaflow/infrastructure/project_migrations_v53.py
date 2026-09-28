from __future__ import annotations

import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v52_to_v53(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=52,
            target_version=53,
            migrate_database=_add_sequence_variants,
        )
        _add_sequence_variants(connection)
        connection.execute(
            "UPDATE schema_info SET version=53 WHERE component='project'"
        )


def _add_sequence_variants(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sequence_variant (
            sequence_id TEXT PRIMARY KEY REFERENCES sequence(id) ON DELETE CASCADE,
            source_sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE RESTRICT,
            preset_id TEXT NOT NULL,
            reframe_mode TEXT NOT NULL CHECK(reframe_mode IN ('fit', 'center_fill')),
            source_timeline_revision INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sequence_variant_source_preset
        ON sequence_variant(source_sequence_id, preset_id, created_at)
        """
    )
