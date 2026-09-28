from __future__ import annotations

import sqlite3

from .project_snapshot_migration import migrate_version_snapshots


def migrate_v56_to_v57(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=56,
            target_version=57,
            migrate_database=_add_v57_project_state,
        )
        _add_v57_project_state(connection)
        connection.execute(
            "UPDATE schema_info SET version=57 WHERE component='project'"
        )


def _add_v57_project_state(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(sequence_variant)")
    }
    additions = {
        "baseline_timeline_json": "TEXT",
        "baseline_audio_buses_json": "TEXT NOT NULL DEFAULT '[]'",
        "baseline_audio_effects_json": "TEXT NOT NULL DEFAULT '[]'",
        "baseline_subtitle_placements_json": "TEXT NOT NULL DEFAULT '[]'",
        "updated_at": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, declaration in additions.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE sequence_variant ADD COLUMN {name} {declaration}"
            )
    connection.execute(
        "UPDATE sequence_variant SET updated_at=created_at WHERE updated_at=0"
    )
    take_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(voiceover_take)")
    }
    take_additions = {
        "latency_compensation_samples": "INTEGER NOT NULL DEFAULT 0",
        "latency_sample_rate": "INTEGER NOT NULL DEFAULT 48000",
        "calibration_device_id": "TEXT",
        "calibration_measured_at": "INTEGER",
    }
    for name, declaration in take_additions.items():
        if name not in take_columns:
            connection.execute(
                f"ALTER TABLE voiceover_take ADD COLUMN {name} {declaration}"
            )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS voiceover_latency_calibration (
            device_id TEXT PRIMARY KEY,
            device_name TEXT NOT NULL,
            latency_samples INTEGER NOT NULL,
            sample_rate INTEGER NOT NULL,
            confidence REAL NOT NULL,
            method TEXT NOT NULL CHECK(method IN ('acoustic_roundtrip', 'manual')),
            measured_at INTEGER NOT NULL
        )
        """
    )
