from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mediaflow.domain.model_base import now_ms

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json


class ProjectOperationRepository(ProjectRepositoryComponent):
    """Durable request receipts and one-time task-result consumption."""

    def result(
        self,
        request_id: str,
        operation: str,
        input_hash: str,
    ) -> dict[str, Any] | None:
        row = self._fetchone(
            """SELECT operation, input_hash, result_json, state
               FROM automation_request WHERE request_id=?""",
            (request_id,),
        )
        if row is None:
            return None
        self._validate_identity(row, request_id, operation, input_hash)
        if row["state"] == "running":
            return None
        if row["state"] != "completed":
            raise RuntimeError(f"Unknown automation request state: {row['state']}")
        return self._result_document(row)

    def is_running(
        self,
        request_id: str,
        operation: str,
        input_hash: str,
    ) -> bool:
        row = self._fetchone(
            """SELECT operation, input_hash, state
               FROM automation_request WHERE request_id=?""",
            (request_id,),
        )
        if row is None:
            return False
        self._validate_identity(row, request_id, operation, input_hash)
        state = str(row["state"])
        if state not in {"running", "completed"}:
            raise RuntimeError(f"Unknown automation request state: {state}")
        return state == "running"

    def begin(
        self,
        request_id: str,
        operation: str,
        input_hash: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO automation_request(
                       request_id, operation, input_hash, result_json,
                       state, created_at
                   ) VALUES (?, ?, ?, '{}', 'running', ?)
                   ON CONFLICT(request_id) DO NOTHING""",
                (request_id, operation, input_hash, now_ms()),
            )
            row = connection.execute(
                """SELECT operation, input_hash, result_json, state
                   FROM automation_request WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Automation request was not persisted")
            self._validate_identity(row, request_id, operation, input_hash)
            retrying = cursor.rowcount == 0
            if row["state"] == "running":
                return None, retrying
            if row["state"] != "completed":
                raise RuntimeError(f"Unknown automation request state: {row['state']}")
            return self._result_document(row), retrying

    def save_result(
        self,
        request_id: str,
        operation: str,
        input_hash: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        payload = _json(result)
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT operation, input_hash, result_json, state
                   FROM automation_request WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            if row is not None:
                self._validate_identity(row, request_id, operation, input_hash)
            if row is None:
                connection.execute(
                    """INSERT INTO automation_request(
                           request_id, operation, input_hash, result_json,
                           state, created_at
                       ) VALUES (?, ?, ?, ?, 'completed', ?)""",
                    (request_id, operation, input_hash, payload, now_ms()),
                )
            elif row["state"] == "running":
                connection.execute(
                    """UPDATE automation_request
                       SET result_json=?, state='completed'
                       WHERE request_id=? AND state='running'""",
                    (payload, request_id),
                )
            elif row["state"] != "completed":
                raise RuntimeError(f"Unknown automation request state: {row['state']}")
            stored = connection.execute(
                """SELECT result_json, state FROM automation_request
                   WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            if stored is None or stored["state"] != "completed":
                raise RuntimeError("Automation result was not persisted")
            return self._result_document(stored)

    def consume_task_result_once(
        self,
        task_id: str,
        project_id: str,
        task_revision: int,
        action: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        with self.transaction() as connection:
            task_row = connection.execute(
                """SELECT project_id, status, revision
                   FROM task WHERE id=?""",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise KeyError(task_id)
            if task_row["project_id"] != project_id or int(task_row["revision"]) != task_revision:
                raise RuntimeError("Task changed before its result could be consumed")
            if task_row["status"] not in {
                "completed",
                "failed",
                "cancelled",
            }:
                raise ValueError("Only terminal task results can be consumed")
            stored_row = connection.execute(
                """SELECT project_id, task_revision, result_json
                   FROM task_consumption WHERE task_id=?""",
                (task_id,),
            ).fetchone()
            if stored_row is not None:
                if (
                    stored_row["project_id"] != project_id
                    or int(stored_row["task_revision"]) != task_revision
                ):
                    raise RuntimeError("Persisted task consumption does not match the task")
                return self._result_document(stored_row), False

            result = action()
            if not isinstance(result, dict):
                raise TypeError("Task result consumer must return a dictionary")
            payload = _json(result)
            connection.execute(
                """INSERT INTO task_consumption(
                       task_id, project_id, task_revision,
                       result_json, created_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (task_id, project_id, task_revision, payload, now_ms()),
            )
            return self._decode_result(payload), True

    def committed_task_result(self, task_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT result_json FROM task_consumption WHERE task_id=?",
            (task_id,),
        )
        return None if row is None else self._result_document(row)

    @staticmethod
    def _validate_identity(
        row: Any,
        request_id: str,
        operation: str,
        input_hash: str,
    ) -> None:
        if row["operation"] != operation or row["input_hash"] != input_hash:
            raise ValueError(f"Automation request_id was reused with different input: {request_id!r}")

    @classmethod
    def _result_document(cls, row: Any) -> dict[str, Any]:
        return cls._decode_result(str(row["result_json"]))

    @staticmethod
    def _decode_result(payload: str) -> dict[str, Any]:
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise RuntimeError("Persisted operation result is not a JSON object")
        return result
