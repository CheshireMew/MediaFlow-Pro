from __future__ import annotations

import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v55_to_v56(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=55,
            target_version=56,
            migrate_database=_add_voiceover_tables,
        )
        _add_voiceover_tables(connection)
        connection.execute(
            "UPDATE schema_info SET version=56 WHERE component='project'"
        )


def _add_voiceover_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS voiceover_cue (
            id TEXT PRIMARY KEY,
            sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
            start_frame INTEGER NOT NULL,
            end_frame INTEGER NOT NULL,
            text TEXT NOT NULL,
            speaker TEXT NOT NULL,
            notes TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('planned', 'recording', 'review', 'approved')),
            selected_take_id TEXT,
            placed_clip_id TEXT,
            archived INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS voiceover_take (
            id TEXT PRIMARY KEY,
            cue_id TEXT NOT NULL REFERENCES voiceover_cue(id) ON DELETE CASCADE,
            asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE RESTRICT,
            name TEXT NOT NULL,
            duration_frames INTEGER NOT NULL,
            notes TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 0 AND 5),
            archived INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_voiceover_cue_sequence_range
        ON voiceover_cue(sequence_id, archived, start_frame, end_frame)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_voiceover_take_cue_time
        ON voiceover_take(cue_id, archived, created_at)
        """
    )
