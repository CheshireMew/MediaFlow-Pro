from __future__ import annotations

import json
import sqlite3

from mediaflow.domain.enums import (
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.project import (
    Project,
)
from mediaflow.domain.workflows import WorkflowRun

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json
from .project_serialization import model_json as _model_json


class ProjectMetadataRepository(ProjectRepositoryComponent):
    def get_project(self) -> Project:
        row = self._fetchone("SELECT * FROM project LIMIT 1")
        if row is None:
            raise RuntimeError("Project record is missing")
        return Project(
            id=row["id"],
            name=row["name"],
            main_sequence_id=row["main_sequence_id"],
            workflow_auto_continue=(
                None if row["workflow_auto_continue"] < 0 else bool(row["workflow_auto_continue"])
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_workflow_auto_continue(self, value: bool | None) -> Project:
        stored = -1 if value is None else int(value)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE project SET workflow_auto_continue=?",
                (stored,),
            )
            self._touch_project(connection)
        return self.get_project()

    def save_workflow_run(self, run: WorkflowRun) -> WorkflowRun:
        project = self.get_project()
        if run.project_id != project.id:
            raise ValueError("Workflow run belongs to another project")
        self._relations.sequences.get_sequence(run.sequence_id)
        if any(
            self._relations.assets.get_asset(asset_id).project_id != project.id for asset_id in run.asset_ids
        ):
            raise ValueError("Workflow run contains an asset from another project")
        with self.transaction() as connection:
            latest_row = connection.execute(
                "SELECT MAX(updated_at) AS updated_at FROM workflow_run"
            ).fetchone()
            latest_updated_at = (
                int(latest_row["updated_at"])
                if latest_row is not None and latest_row["updated_at"] is not None
                else -1
            )
            updated = run.model_copy(update={"updated_at": max(now_ms(), latest_updated_at + 1)})
            connection.execute(
                """INSERT INTO workflow_run(
                    id, project_id, sequence_id, asset_ids_json, stage, status,
                    auto_continue, payload_json, message_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    sequence_id=excluded.sequence_id,
                    asset_ids_json=excluded.asset_ids_json,
                    stage=excluded.stage,
                    status=excluded.status,
                    auto_continue=excluded.auto_continue,
                    payload_json=excluded.payload_json,
                    message_code=excluded.message_code,
                    updated_at=excluded.updated_at""",
                (
                    updated.id,
                    updated.project_id,
                    updated.sequence_id,
                    _json(updated.asset_ids),
                    updated.stage.value,
                    updated.status.value,
                    int(updated.auto_continue),
                    _model_json(updated.payload),
                    updated.message_code,
                    updated.created_at,
                    updated.updated_at,
                ),
            )
            self._touch_project(connection)
        return self.get_workflow_run(updated.id)

    def get_workflow_run(self, run_id: str) -> WorkflowRun:
        row = self._fetchone("SELECT * FROM workflow_run WHERE id=?", (run_id,))
        if row is None:
            raise KeyError(run_id)
        return self._workflow_run_from_row(row)

    def list_workflow_runs(self, *, active_only: bool = False) -> list[WorkflowRun]:
        sql = "SELECT * FROM workflow_run"
        parameters: tuple = ()
        if active_only:
            sql += " WHERE status NOT IN ('completed', 'cancelled')"
        sql += " ORDER BY updated_at DESC, id"
        return [self._workflow_run_from_row(row) for row in self._fetchall(sql, parameters)]

    @staticmethod
    def _workflow_run_from_row(row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(
            id=row["id"],
            project_id=row["project_id"],
            sequence_id=row["sequence_id"],
            asset_ids=json.loads(row["asset_ids_json"]),
            stage=WorkflowStage(row["stage"]),
            status=WorkflowStatus(row["status"]),
            auto_continue=bool(row["auto_continue"]),
            payload=json.loads(row["payload_json"]),
            message_code=row["message_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
