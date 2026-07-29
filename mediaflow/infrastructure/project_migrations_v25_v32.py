from __future__ import annotations

import json
from pathlib import Path

from mediaflow.domain.settings import AsrSettings
from mediaflow.infrastructure.project_serialization import json_value as _json


def migrate_v25_to_v26(workspace) -> None:
    with workspace.transaction() as connection:
        task_rows = connection.execute(
            """SELECT id, sequence_id, command_json, status
               FROM task"""
        ).fetchall()
        for task_row in task_rows:
            command = json.loads(str(task_row["command_json"]))
            if command.get("command_type") != "transcribe_sequence":
                continue
            if "plan" not in command:
                sequence_id = str(command.pop("sequence_id", None) or task_row["sequence_id"] or "")
                sequence_row = connection.execute(
                    """SELECT fps_numerator, fps_denominator
                       FROM sequence WHERE id=?""",
                    (sequence_id,),
                ).fetchone()
                command["plan"] = {
                    "sequence_id": sequence_id,
                    "timeline_signature": "legacy",
                    "dialogue_track_id": "legacy",
                    "timeline_start_frame": 0,
                    "timeline_end_frame": 0,
                    "fps_numerator": (int(sequence_row["fps_numerator"]) if sequence_row is not None else 30),
                    "fps_denominator": (
                        int(sequence_row["fps_denominator"]) if sequence_row is not None else 1
                    ),
                    "sources": [],
                    "asr": AsrSettings().model_dump(mode="json"),
                }
            status = str(task_row["status"])
            update_values: list[object] = [_json(command)]
            update_clause = "command_json=?"
            if status in {"pending", "running", "paused"} and not command["plan"].get("sources"):
                update_clause += ", status='cancelled', progress_json=?, error=?"
                update_values.extend(
                    [
                        _json(
                            {
                                "mode": "indeterminate",
                                "message_code": "cancelled",
                                "completed": None,
                                "total": None,
                                "unit": None,
                            }
                        ),
                        "旧版转录任务缺少可复现计划，请重新发起转录",
                    ]
                )
            update_values.append(task_row["id"])
            connection.execute(
                f"UPDATE task SET {update_clause} WHERE id=?",
                tuple(update_values),
            )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (26,),
        )


def migrate_v26_to_v27(workspace) -> None:
    with workspace.transaction() as connection:
        sequence_columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(sequence)").fetchall()
        }
        if "timeline_revision" not in sequence_columns:
            connection.execute("ALTER TABLE sequence ADD COLUMN timeline_revision INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (27,),
        )


def migrate_v27_to_v28(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS task_event (
                cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL,
                task_revision INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_event_project_cursor ON task_event(project_id, cursor)"
        )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (28,),
        )


def migrate_v28_to_v29(workspace) -> None:
    with workspace.transaction() as connection:
        project_dir = Path(workspace.project_dir).resolve()
        for task_row in connection.execute("SELECT id, artifacts_json FROM task").fetchall():
            values = json.loads(str(task_row["artifacts_json"]))
            references: list[dict[str, str]] = []
            for value in values:
                path = Path(str(value))
                if path.is_absolute():
                    try:
                        relative = path.resolve().relative_to(project_dir)
                    except ValueError:
                        references.append({"scope": "external", "path": str(path.resolve())})
                    else:
                        references.append({"scope": "project", "path": relative.as_posix()})
                else:
                    references.append({"scope": "project", "path": path.as_posix()})
            connection.execute(
                "UPDATE task SET artifacts_json=? WHERE id=?",
                (_json(references), task_row["id"]),
            )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (29,),
        )


def migrate_v29_to_v30(workspace) -> None:
    with workspace.transaction() as connection:
        project_columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(project)").fetchall()
        }
        if "root_path" in project_columns:
            connection.execute("ALTER TABLE project DROP COLUMN root_path")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (30,),
        )


def migrate_v30_to_v31(workspace) -> None:
    with workspace.transaction() as connection:
        task_columns = {item["name"] for item in connection.execute("PRAGMA table_info(task)").fetchall()}
        if "idempotency_key" not in task_columns:
            connection.execute("ALTER TABLE task ADD COLUMN idempotency_key TEXT")
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_task_project_idempotency
               ON task(project_id, idempotency_key)
               WHERE idempotency_key IS NOT NULL"""
        )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (31,),
        )


def migrate_v31_to_v32(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS automation_request (
                   request_id TEXT PRIMARY KEY,
                   operation TEXT NOT NULL,
                   input_hash TEXT NOT NULL,
                   result_json TEXT NOT NULL,
                   created_at INTEGER NOT NULL
               )"""
        )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (32,),
        )


def migrate_v32_to_v33(workspace) -> None:
    with workspace.transaction() as connection:
        task_columns = {item["name"] for item in connection.execute("PRAGMA table_info(task)").fetchall()}
        if "outcome_json" not in task_columns:
            connection.execute("ALTER TABLE task ADD COLUMN outcome_json TEXT")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (33,),
        )
