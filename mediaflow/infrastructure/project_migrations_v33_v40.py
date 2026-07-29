from __future__ import annotations

import json


def migrate_v33_to_v34(workspace) -> None:
    with workspace.transaction() as connection:
        automation_columns = {
            item["name"]
            for item in connection.execute(
                "PRAGMA table_info(automation_request)"
            ).fetchall()
        }
        if "state" not in automation_columns:
            connection.execute(
                """ALTER TABLE automation_request
                   ADD COLUMN state TEXT NOT NULL DEFAULT 'completed'"""
            )

        task_columns = {
            item["name"]
            for item in connection.execute("PRAGMA table_info(task)").fetchall()
        }
        for column, definition in (
            ("execution_owner_id", "TEXT"),
            ("heartbeat_at", "INTEGER"),
            ("lease_expires_at", "INTEGER"),
            ("stop_request", "TEXT"),
        ):
            if column not in task_columns:
                connection.execute(
                    f"ALTER TABLE task ADD COLUMN {column} {definition}"
                )

        running_rows = connection.execute(
            "SELECT id FROM task WHERE status='running'"
        ).fetchall()
        for row in running_rows:
            owner_id = f"expired:migration:v34:{row['id']}"
            connection.execute(
                """UPDATE task
                   SET execution_owner_id=?, heartbeat_at=0, lease_expires_at=1,
                       stop_request=NULL
                   WHERE id=?""",
                (owner_id, row["id"]),
            )

        for row in connection.execute(
            """SELECT cursor, task_id, payload_json
               FROM task_event"""
        ).fetchall():
            payload = json.loads(str(row["payload_json"]))
            if payload.get("status") != "running":
                continue
            payload["execution_owner_id"] = (
                f"expired:migration:v34:{row['task_id']}"
            )
            payload["heartbeat_at"] = 0
            payload["lease_expires_at"] = 1
            payload["stop_request"] = None
            connection.execute(
                "UPDATE task_event SET payload_json=? WHERE cursor=?",
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    row["cursor"],
                ),
            )

        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (34,),
        )


def migrate_v34_to_v35(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS task_consumption (
                   task_id TEXT PRIMARY KEY
                       REFERENCES task(id) ON DELETE CASCADE,
                   project_id TEXT NOT NULL
                       REFERENCES project(id) ON DELETE CASCADE,
                   task_revision INTEGER NOT NULL,
                   result_json TEXT NOT NULL,
                   created_at INTEGER NOT NULL
               )"""
        )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (35,),
        )
