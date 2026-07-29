from __future__ import annotations

import json
from typing import Any

from mediaflow.domain.enums import TrackKind
from mediaflow.infrastructure.project_serialization import json_value as _json
from mediaflow.infrastructure.project_serialization import model_json as _model_json
from mediaflow.infrastructure.storage_paths import default_media_root
from mediaflow.infrastructure.task_command_migrations import migrate_stored_task_command


def migrate_v9_to_v10(workspace) -> None:
    with workspace.transaction() as connection:
        columns = {item["name"] for item in connection.execute("PRAGMA table_info(sequence)").fetchall()}
        if "in_frame" not in columns:
            connection.execute("ALTER TABLE sequence ADD COLUMN in_frame INTEGER")
        if "out_frame" not in columns:
            connection.execute("ALTER TABLE sequence ADD COLUMN out_frame INTEGER")

        preset_rows = connection.execute(
            "SELECT sequence_id, preset_json FROM sequence_export_setting"
        ).fetchall()
        for preset_row in preset_rows:
            preset = json.loads(preset_row["preset_json"])
            legacy_trim = preset.pop("trim", None)
            if isinstance(legacy_trim, dict):
                duration_row = connection.execute(
                    """SELECT COALESCE(MAX(clip.timeline_start + clip.duration), 1) AS duration
                       FROM clip
                       JOIN track ON track.id=clip.track_id
                       WHERE track.sequence_id=?""",
                    (preset_row["sequence_id"],),
                ).fetchone()
                duration = max(1, int(duration_row["duration"]))
                start = max(0, min(duration, int(legacy_trim.get("start_frame") or 0)))
                end = max(1, min(duration, int(legacy_trim.get("end_frame") or duration)))
                if legacy_trim.get("auto_trim_silence"):
                    speech = connection.execute(
                        """SELECT MIN(placement.start_frame) AS first_speech,
                                  MAX(placement.end_frame) AS last_speech
                           FROM subtitle_placement placement
                           JOIN track ON track.id=placement.track_id
                           WHERE track.sequence_id=? AND track.kind=? AND track.enabled=1""",
                        (preset_row["sequence_id"], TrackKind.SUBTITLE.value),
                    ).fetchone()
                    if speech["first_speech"] is not None:
                        start = max(start, int(speech["first_speech"]))
                        end = min(end, int(speech["last_speech"]))
                if end > start and (
                    legacy_trim.get("start_frame") is not None
                    or legacy_trim.get("end_frame") is not None
                    or legacy_trim.get("auto_trim_silence")
                ):
                    connection.execute(
                        "UPDATE sequence SET in_frame=?, out_frame=? WHERE id=?",
                        (start, end, preset_row["sequence_id"]),
                    )
            connection.execute(
                "UPDATE sequence_export_setting SET preset_json=? WHERE sequence_id=?",
                (_json(preset), preset_row["sequence_id"]),
            )

        for preset_row in connection.execute("SELECT id, data_json FROM export_preset").fetchall():
            preset = json.loads(preset_row["data_json"])
            preset.pop("trim", None)
            connection.execute(
                "UPDATE export_preset SET data_json=? WHERE id=?",
                (_json(preset), preset_row["id"]),
            )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (10,),
        )


def migrate_v10_to_v11(workspace) -> None:
    with workspace.transaction() as connection:
        columns = {item["name"] for item in connection.execute("PRAGMA table_info(sequence)").fetchall()}
        if "archived" not in columns:
            connection.execute("ALTER TABLE sequence ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (11,),
        )


def migrate_v11_to_v12(workspace) -> None:
    task_columns = {item["name"] for item in workspace._fetchall("PRAGMA table_info(task)")}
    if "command_json" in task_columns:
        with workspace.transaction() as connection:
            connection.execute(
                "UPDATE schema_info SET version=? WHERE component='project'",
                (12,),
            )
        return
    with workspace.transaction() as connection:
        connection.execute(
            """CREATE TABLE task_v12 (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                sequence_id TEXT REFERENCES sequence(id) ON DELETE SET NULL,
                command_json TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL,
                message_code TEXT NOT NULL,
                input_asset_ids_json TEXT NOT NULL,
                artifacts_json TEXT NOT NULL,
                execution_trace_json TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                revision INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        rows = connection.execute("SELECT * FROM task ORDER BY created_at, id").fetchall()
        connection.executemany(
            """INSERT INTO task_v12(
                id, project_id, sequence_id, command_json, status, progress,
                message_code, input_asset_ids_json, artifacts_json,
                execution_trace_json, error, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    task_row["id"],
                    task_row["project_id"],
                    task_row["sequence_id"],
                    _model_json(
                        migrate_stored_task_command(
                            str(task_row["kind"]),
                            json.loads(task_row["parameters_json"]),
                            sequence_id=task_row["sequence_id"],
                        )
                    ),
                    task_row["status"],
                    task_row["progress"],
                    task_row["message_code"],
                    task_row["input_asset_ids_json"],
                    task_row["artifacts_json"],
                    task_row["execution_trace_json"],
                    task_row["error"],
                    task_row["revision"],
                    task_row["created_at"],
                    task_row["updated_at"],
                )
                for task_row in rows
            ],
        )
        connection.execute("DROP TABLE task")
        connection.execute("ALTER TABLE task_v12 RENAME TO task")
        connection.execute("CREATE INDEX idx_task_project_time ON task(project_id, created_at)")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (12,),
        )


def migrate_v12_to_v13(workspace) -> None:
    media_root = default_media_root()
    with workspace.transaction() as connection:
        for task_row in connection.execute("SELECT id, command_json FROM task").fetchall():
            command = json.loads(task_row["command_json"])
            if command.get("command_type") != "download_media":
                continue
            request = command.get("request")
            if isinstance(request, dict) and not str(request.get("output_directory") or "").strip():
                request["output_directory"] = media_root
                connection.execute(
                    "UPDATE task SET command_json=? WHERE id=?",
                    (_json(command), task_row["id"]),
                )
        for run_row in connection.execute("SELECT id, payload_json FROM workflow_run").fetchall():
            payload = json.loads(run_row["payload_json"])
            changed = False
            for request in payload.get("requests") or []:
                if isinstance(request, dict) and not str(request.get("output_directory") or "").strip():
                    request["output_directory"] = media_root
                    changed = True
            if changed:
                connection.execute(
                    "UPDATE workflow_run SET payload_json=? WHERE id=?",
                    (_json(payload), run_row["id"]),
                )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (13,),
        )


def migrate_v13_to_v14(workspace) -> None:
    with workspace.transaction() as connection:
        columns = {item["name"] for item in connection.execute("PRAGMA table_info(sequence)").fetchall()}
        if "profile_confirmed" not in columns:
            connection.execute("ALTER TABLE sequence ADD COLUMN profile_confirmed INTEGER NOT NULL DEFAULT 1")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (14,),
        )


def migrate_v14_to_v15(workspace) -> None:
    with workspace.transaction() as connection:
        columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(subtitle_placement)").fetchall()
        }
        if "timing_overridden" not in columns:
            connection.execute(
                "ALTER TABLE subtitle_placement ADD COLUMN timing_overridden INTEGER NOT NULL DEFAULT 0"
            )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (15,),
        )


def migrate_v15_to_v16(workspace) -> None:
    with workspace.transaction() as connection:
        document_columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(subtitle_document)").fetchall()
        }
        if "sequence_id" not in document_columns:
            connection.execute(
                "ALTER TABLE subtitle_document ADD COLUMN sequence_id TEXT "
                "REFERENCES sequence(id) ON DELETE CASCADE"
            )
        project_row = connection.execute("SELECT main_sequence_id FROM project LIMIT 1").fetchone()
        main_sequence_id = str(project_row["main_sequence_id"]) if project_row else ""
        for task_row in connection.execute("SELECT id, sequence_id, command_json FROM task").fetchall():
            command = json.loads(task_row["command_json"])
            if command.get("command_type") not in {
                "transcribe_asset",
                "transcribe_region",
            }:
                continue
            sequence_id = str(task_row["sequence_id"] or main_sequence_id)
            migrated: dict[str, Any] = {
                "command_type": "transcribe_sequence",
                "sequence_id": sequence_id,
            }
            if command.get("workflow"):
                migrated["workflow"] = command["workflow"]
            connection.execute(
                "UPDATE task SET command_json=? WHERE id=?",
                (_json(migrated), task_row["id"]),
            )
        for run_row in connection.execute("SELECT id, payload_json FROM workflow_run").fetchall():
            payload = json.loads(run_row["payload_json"])
            if "document_ids_before_transcribe" not in payload:
                continue
            payload.pop("document_ids_before_transcribe", None)
            connection.execute(
                "UPDATE workflow_run SET payload_json=? WHERE id=?",
                (_json(payload), run_row["id"]),
            )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (16,),
        )


def migrate_v16_to_v17(workspace) -> None:
    with workspace.transaction() as connection:
        project_columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(project)").fetchall()
        }
        if "content_revision" not in project_columns:
            connection.execute("ALTER TABLE project ADD COLUMN content_revision INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS web_asset (
                   asset_id TEXT PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
                   manifest_json TEXT NOT NULL,
                   source_hash TEXT NOT NULL
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS web_clip_state (
                   clip_id TEXT PRIMARY KEY REFERENCES clip(id) ON DELETE CASCADE,
                   state_json TEXT NOT NULL,
                   revision INTEGER NOT NULL
               )"""
        )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (17,),
        )
