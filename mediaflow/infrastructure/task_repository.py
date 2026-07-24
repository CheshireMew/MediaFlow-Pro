from __future__ import annotations

import builtins
import json
import sqlite3
from pathlib import Path

from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.tasks import Task
from mediaflow.infrastructure.project_schema import PROJECT_FILE_NAME


class TaskRepository:
    """Thread-safe task persistence using one short SQLite connection per operation."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve(strict=True)
        self.database_path = self.project_dir / PROJECT_FILE_NAME
        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def create(self, task: Task) -> Task:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO task(
                    id, project_id, sequence_id, command_json, status, progress_json,
                    input_asset_ids_json,
                    artifacts_json, execution_trace_json, error, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._values(task),
            )
        return self.get(task.id)

    def save(self, task: Task) -> Task:
        current = self.get(task.id)
        next_task = task.model_copy(update={"revision": current.revision + 1, "updated_at": now_ms()})
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE task SET
                    project_id=?, sequence_id=?, command_json=?, status=?, progress_json=?,
                    input_asset_ids_json=?,
                    artifacts_json=?, execution_trace_json=?, error=?, revision=?, created_at=?, updated_at=?
                WHERE id=? AND revision=?""",
                (
                    next_task.project_id,
                    next_task.sequence_id,
                    self._json(
                        next_task.command.model_dump(
                            mode="json",
                            exclude_computed_fields=True,
                        )
                    ),
                    next_task.status.value,
                    self._json(
                        next_task.progress.model_dump(
                            mode="json",
                            exclude_computed_fields=True,
                        )
                    ),
                    self._json(next_task.input_asset_ids),
                    self._json(next_task.artifacts),
                    self._json([item.model_dump(mode="json") for item in next_task.execution_trace]),
                    next_task.error,
                    next_task.revision,
                    next_task.created_at,
                    next_task.updated_at,
                    next_task.id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Concurrent task update rejected: {task.id}")
        return self.get(task.id)

    def get(self, task_id: str) -> Task:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM task WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._from_row(row)

    def list(self) -> builtins.list[Task]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM task ORDER BY created_at, id").fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, task_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM task WHERE id=?", (task_id,))
            if cursor.rowcount != 1:
                raise KeyError(task_id)

    def delete_terminal(self) -> builtins.list[Task]:
        tasks = [task for task in self.list() if task.status.is_terminal]
        if not tasks:
            return []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "DELETE FROM task WHERE id=?",
                [(task.id,) for task in tasks],
            )
        return tasks

    def recover_interrupted(self) -> builtins.list[Task]:
        recovered: builtins.list[Task] = []
        for task in self.list():
            if task.status.is_in_flight:
                recovered.append(
                    self.save(
                        task.model_copy(
                            update={
                                "status": TaskStatus.PAUSED,
                                "progress": OperationProgress.indeterminate(
                                    "interrupted_by_restart"
                                ),
                                "error": None,
                            }
                        )
                    )
                )
        return recovered

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _values(cls, task: Task) -> tuple[object, ...]:
        return (
            task.id,
            task.project_id,
            task.sequence_id,
            cls._json(
                task.command.model_dump(
                    mode="json",
                    exclude_computed_fields=True,
                )
            ),
            task.status.value,
            cls._json(
                task.progress.model_dump(
                    mode="json",
                    exclude_computed_fields=True,
                )
            ),
            cls._json(task.input_asset_ids),
            cls._json(task.artifacts),
            cls._json([item.model_dump(mode="json") for item in task.execution_trace]),
            task.error,
            task.revision,
            task.created_at,
            task.updated_at,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            project_id=row["project_id"],
            sequence_id=row["sequence_id"],
            command=json.loads(row["command_json"]),
            status=TaskStatus(row["status"]),
            progress=json.loads(row["progress_json"]),
            input_asset_ids=json.loads(row["input_asset_ids_json"]),
            artifacts=json.loads(row["artifacts_json"]),
            execution_trace=json.loads(row["execution_trace_json"]),
            error=row["error"],
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
