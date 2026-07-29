from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from mediaflow.application.events import TaskEvent
from mediaflow.application.ports import TaskProjectAccess
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.tasks import Task, TaskStopRequest
from mediaflow.infrastructure.project_schema_definition import PROJECT_FILE_NAME
from mediaflow.infrastructure.sqlite_uri import read_only_database_uri


class TaskRepository:
    """Thread-safe task persistence using one short SQLite connection per operation."""

    def __init__(
        self,
        project: TaskProjectAccess,
    ):
        self.project_dir = Path(project.project_dir).resolve(strict=True)
        self.database_path = self.project_dir / PROJECT_FILE_NAME
        self.read_only = project.read_only
        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = (
            sqlite3.connect(
                read_only_database_uri(self.database_path),
                uri=True,
                timeout=5.0,
            )
            if self.read_only
            else sqlite3.connect(self.database_path, timeout=5.0)
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection, connection:
            yield connection

    def create(self, task: Task, *, event_type: str = "created") -> Task:
        self._require_writable()
        task = self._validated(task)
        try:
            return self._insert(task, event_type=event_type)
        except sqlite3.IntegrityError:
            if not task.idempotency_key:
                raise
            with self._connection() as connection:
                row = connection.execute(
                    """SELECT * FROM task
                       WHERE project_id=? AND idempotency_key=?""",
                    (task.project_id, task.idempotency_key),
                ).fetchone()
            if row is None:
                raise
            return self._from_row(row)

    def _insert(self, task: Task, *, event_type: str) -> Task:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO task(
                    id, project_id, sequence_id, idempotency_key,
                    command_json, status, progress_json, input_asset_ids_json,
                    artifacts_json, outcome_json, execution_trace_json,
                    error, execution_owner_id, heartbeat_at, lease_expires_at,
                    stop_request, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._values(task),
            )
            self._insert_event(connection, task, event_type)
        return self.get(task.id)

    def claim(
        self,
        task_id: str,
        owner_id: str,
        lease_duration_ms: int,
    ) -> tuple[Task, bool] | None:
        self._require_writable()
        if not owner_id.strip():
            raise ValueError("Task execution owner cannot be empty")
        if lease_duration_ms <= 0:
            raise ValueError("Task lease duration must be positive")
        claimed_at = now_ms()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = self._from_row(row)
            recovered = task.status == TaskStatus.RUNNING
            if task.status == TaskStatus.PENDING:
                claimable = task.execution_owner_id is None
            elif recovered:
                claimable = (
                    task.lease_expires_at is not None
                    and task.lease_expires_at <= claimed_at
                )
            else:
                claimable = False
            if not claimable:
                return None
            claimed = self._validated(
                task.model_copy(
                    update={
                        "status": TaskStatus.RUNNING,
                        "progress": OperationProgress.indeterminate(
                            "resuming_after_restart" if recovered else "running"
                        ),
                        "error": None,
                        "execution_owner_id": owner_id,
                        "heartbeat_at": claimed_at,
                        "lease_expires_at": claimed_at + lease_duration_ms,
                        "revision": task.revision + 1,
                        "updated_at": claimed_at,
                    }
                )
            )
            cursor = connection.execute(
                """UPDATE task SET
                    status=?, progress_json=?, error=?,
                    execution_owner_id=?, heartbeat_at=?, lease_expires_at=?,
                    revision=?, updated_at=?
                WHERE id=? AND revision=? AND (
                    (status='pending' AND execution_owner_id IS NULL)
                    OR (status='running' AND lease_expires_at<=?)
                )""",
                (
                    claimed.status.value,
                    self._json(
                        claimed.progress.model_dump(
                            mode="json",
                            exclude_computed_fields=True,
                        )
                    ),
                    claimed.error,
                    claimed.execution_owner_id,
                    claimed.heartbeat_at,
                    claimed.lease_expires_at,
                    claimed.revision,
                    claimed.updated_at,
                    claimed.id,
                    task.revision,
                    claimed_at,
                ),
            )
            if cursor.rowcount != 1:
                return None
            self._insert_event(connection, claimed, "status")
        return claimed, recovered

    def renew_lease(
        self,
        task_id: str,
        owner_id: str,
        lease_duration_ms: int,
    ) -> Task | None:
        self._require_writable()
        if lease_duration_ms <= 0:
            raise ValueError("Task lease duration must be positive")
        heartbeat_at = now_ms()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE task
                   SET heartbeat_at=?, lease_expires_at=?
                   WHERE id=? AND status='running'
                     AND execution_owner_id=?""",
                (
                    heartbeat_at,
                    heartbeat_at + lease_duration_ms,
                    task_id,
                    owner_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM task WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            return self._from_row(row)

    def update_owned(
        self,
        task: Task,
        owner_id: str,
        *,
        event_type: str,
        release_owner: bool = False,
    ) -> Task | None:
        self._require_writable()
        updated_at = now_ms()
        next_task = task.model_copy(
            update={
                "execution_owner_id": (
                    None if release_owner else task.execution_owner_id
                ),
                "heartbeat_at": None if release_owner else task.heartbeat_at,
                "lease_expires_at": (
                    None if release_owner else task.lease_expires_at
                ),
                "stop_request": None if release_owner else task.stop_request,
                "revision": task.revision + 1,
                "updated_at": updated_at,
            }
        )
        next_task = self._validated(next_task)
        lease_clause = (
            "execution_owner_id=NULL, heartbeat_at=NULL, "
            "lease_expires_at=NULL, stop_request=NULL,"
            if release_owner
            else "stop_request=?,"
        )
        parameters: list[object] = [
            next_task.project_id,
            next_task.sequence_id,
            next_task.idempotency_key,
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
            self._json(
                [
                    item.model_dump(mode="json")
                    for item in next_task.artifacts
                ]
            ),
            (
                self._json(next_task.outcome.model_dump(mode="json"))
                if next_task.outcome is not None
                else None
            ),
            self._json(
                [
                    item.model_dump(mode="json")
                    for item in next_task.execution_trace
                ]
            ),
            next_task.error,
        ]
        if not release_owner:
            parameters.append(next_task.stop_request)
        parameters.extend(
            (
                next_task.revision,
                next_task.created_at,
                next_task.updated_at,
                next_task.id,
                task.revision,
                owner_id,
            )
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"""UPDATE task SET
                    project_id=?, sequence_id=?, idempotency_key=?,
                    command_json=?, status=?, progress_json=?,
                    input_asset_ids_json=?,
                    artifacts_json=?, outcome_json=?, execution_trace_json=?,
                    error=?, {lease_clause}
                    revision=?, created_at=?, updated_at=?
                    WHERE id=? AND revision=?
                      AND status='running' AND execution_owner_id=?""",
                tuple(parameters),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM task WHERE id=?",
                (task.id,),
            ).fetchone()
            if row is None:
                return None
            persisted = self._from_row(row)
            self._insert_event(connection, persisted, event_type)
            return persisted

    def queue_paused(self, task_id: str) -> Task | None:
        self._require_writable()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = self._from_row(row)
            if task.status != TaskStatus.PAUSED:
                return None
            queued = self._validated(
                task.model_copy(
                    update={
                        "status": TaskStatus.PENDING,
                        "progress": OperationProgress.indeterminate("queued"),
                        "error": None,
                        "stop_request": None,
                        "revision": task.revision + 1,
                        "updated_at": now_ms(),
                    }
                )
            )
            cursor = connection.execute(
                """UPDATE task
                   SET status=?, progress_json=?, error=NULL,
                       stop_request=NULL, revision=?, updated_at=?
                   WHERE id=? AND revision=? AND status='paused'
                     AND execution_owner_id IS NULL""",
                (
                    queued.status.value,
                    self._json(
                        queued.progress.model_dump(
                            mode="json",
                            exclude_computed_fields=True,
                        )
                    ),
                    queued.revision,
                    queued.updated_at,
                    task.id,
                    task.revision,
                ),
            )
            if cursor.rowcount != 1:
                return None
            self._insert_event(connection, queued, "status")
            return queued

    def request_stop(
        self,
        task_id: str,
        request: TaskStopRequest,
    ) -> Task | None:
        self._require_writable()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = self._from_row(row)
            if task.status == TaskStatus.RUNNING:
                if task.stop_request == "cancel" or task.stop_request == request:
                    return None
                requested = self._validated(
                    task.model_copy(
                        update={
                            "stop_request": request,
                            "revision": task.revision + 1,
                            "updated_at": now_ms(),
                        }
                    )
                )
                cursor = connection.execute(
                    """UPDATE task
                       SET stop_request=?, revision=?, updated_at=?
                       WHERE id=? AND revision=? AND status='running'""",
                    (
                        requested.stop_request,
                        requested.revision,
                        requested.updated_at,
                        task.id,
                        task.revision,
                    ),
                )
                event_type = "control"
            elif task.status == TaskStatus.PENDING or (
                task.status == TaskStatus.PAUSED and request == "cancel"
            ):
                target = (
                    TaskStatus.PAUSED
                    if request == "pause"
                    else TaskStatus.CANCELLED
                )
                requested = self._validated(
                    task.model_copy(
                        update={
                            "status": target,
                            "progress": OperationProgress.indeterminate(
                                target.value
                            ),
                            "stop_request": None,
                            "revision": task.revision + 1,
                            "updated_at": now_ms(),
                        }
                    )
                )
                cursor = connection.execute(
                    """UPDATE task
                       SET status=?, progress_json=?, stop_request=NULL,
                           revision=?, updated_at=?
                       WHERE id=? AND revision=?
                         AND status IN ('pending', 'paused')
                         AND execution_owner_id IS NULL""",
                    (
                        requested.status.value,
                        self._json(
                            requested.progress.model_dump(
                                mode="json",
                                exclude_computed_fields=True,
                            )
                        ),
                        requested.revision,
                        requested.updated_at,
                        task.id,
                        task.revision,
                    ),
                )
                event_type = "status"
            else:
                return None
            if cursor.rowcount != 1:
                return None
            self._insert_event(connection, requested, event_type)
            return requested

    def get(self, task_id: str) -> Task:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM task WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._from_row(row)

    def list(self) -> builtins.list[Task]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM task ORDER BY created_at, id").fetchall()
        return [self._from_row(row) for row in rows]

    def list_unconsumed_terminal(self) -> builtins.list[Task]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT task.*
                   FROM task
                   LEFT JOIN task_consumption
                     ON task_consumption.task_id=task.id
                   WHERE task.status IN ('completed', 'failed', 'cancelled')
                     AND task_consumption.task_id IS NULL
                   ORDER BY task.created_at, task.id"""
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_claimable(self, at_ms: int) -> builtins.list[Task]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM task
                   WHERE (status='pending' AND execution_owner_id IS NULL)
                      OR (status='running' AND lease_expires_at<=?)
                   ORDER BY created_at, id""",
                (at_ms,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, task_id: str, *, event_type: str = "deleted") -> None:
        self._require_writable()
        task = self.get(task_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM task WHERE id=?", (task_id,))
            if cursor.rowcount != 1:
                raise KeyError(task_id)
            self._insert_event(connection, task, event_type)

    def delete_terminal(self) -> builtins.list[Task]:
        self._require_writable()
        tasks = [task for task in self.list() if task.status.is_terminal]
        if not tasks:
            return []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for task in tasks:
                cursor = connection.execute("DELETE FROM task WHERE id=?", (task.id,))
                if cursor.rowcount == 1:
                    self._insert_event(connection, task, "deleted")
        return tasks

    def snapshot(self) -> tuple[builtins.list[Task], int]:
        with self._connection() as connection:
            connection.execute("BEGIN")
            cursor_row = connection.execute(
                "SELECT COALESCE(MAX(cursor), 0) AS cursor FROM task_event"
            ).fetchone()
            rows = connection.execute("SELECT * FROM task ORDER BY created_at, id").fetchall()
        return [self._from_row(row) for row in rows], int(cursor_row["cursor"])

    def latest_event(self, task_id: str) -> TaskEvent:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM task_event
                   WHERE task_id=?
                   ORDER BY cursor DESC
                   LIMIT 1""",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"No persisted event for task {task_id}")
        return self._event_from_row(row)

    def events_after(self, cursor: int, *, limit: int = 500) -> builtins.list[TaskEvent]:
        if cursor < 0:
            raise ValueError("Task event cursor cannot be negative")
        if limit <= 0:
            raise ValueError("Task event limit must be positive")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM task_event
                   WHERE cursor>?
                   ORDER BY cursor
                   LIMIT ?""",
                (cursor, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _values(cls, task: Task) -> tuple[object, ...]:
        return (
            task.id,
            task.project_id,
            task.sequence_id,
            task.idempotency_key,
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
            cls._json([item.model_dump(mode="json") for item in task.artifacts]),
            (cls._json(task.outcome.model_dump(mode="json")) if task.outcome is not None else None),
            cls._json([item.model_dump(mode="json") for item in task.execution_trace]),
            task.error,
            task.execution_owner_id,
            task.heartbeat_at,
            task.lease_expires_at,
            task.stop_request,
            task.revision,
            task.created_at,
            task.updated_at,
        )

    @classmethod
    def _insert_event(
        cls,
        connection: sqlite3.Connection,
        task: Task,
        event_type: str,
    ) -> int:
        cursor = connection.execute(
            """INSERT INTO task_event(
                project_id, task_id, task_revision, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                task.project_id,
                task.id,
                task.revision,
                event_type,
                cls._json(
                    task.model_dump(
                        mode="json",
                        exclude_computed_fields=True,
                    )
                ),
                now_ms(),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Task event insert did not return a cursor")
        return int(cursor.lastrowid)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            project_id=row["project_id"],
            sequence_id=row["sequence_id"],
            idempotency_key=row["idempotency_key"],
            command=json.loads(row["command_json"]),
            status=TaskStatus(row["status"]),
            progress=json.loads(row["progress_json"]),
            input_asset_ids=json.loads(row["input_asset_ids_json"]),
            artifacts=json.loads(row["artifacts_json"]),
            outcome=(json.loads(row["outcome_json"]) if row["outcome_json"] is not None else None),
            execution_trace=json.loads(row["execution_trace_json"]),
            error=row["error"],
            execution_owner_id=row["execution_owner_id"],
            heartbeat_at=row["heartbeat_at"],
            lease_expires_at=row["lease_expires_at"],
            stop_request=row["stop_request"],
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            task_id=row["task_id"],
            project_id=row["project_id"],
            event_type=row["event_type"],
            revision=row["task_revision"],
            payload=json.loads(row["payload_json"]),
            cursor=row["cursor"],
        )

    @staticmethod
    def _validated(task: Task) -> Task:
        return Task.model_validate(
            task.model_dump(
                mode="python",
                exclude_computed_fields=True,
            )
        )

    def _require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("Project task storage is read-only")
