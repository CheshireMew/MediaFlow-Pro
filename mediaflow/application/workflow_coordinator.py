from __future__ import annotations

from mediaflow.domain.enums import WorkflowStage, WorkflowStatus
from mediaflow.domain.models import WorkflowRun
from mediaflow.infrastructure.project_repository import ProjectRepository

_NEXT_STAGE = {
    WorkflowStage.DOWNLOAD: WorkflowStage.PREPARE_MEDIA,
    WorkflowStage.PREPARE_MEDIA: WorkflowStage.TRANSCRIBE,
    WorkflowStage.TRANSCRIBE: WorkflowStage.TRANSLATE,
    WorkflowStage.TRANSLATE: WorkflowStage.HIGHLIGHT,
    WorkflowStage.HIGHLIGHT: WorkflowStage.CREATE_SHORTS,
    WorkflowStage.CREATE_SHORTS: WorkflowStage.EXPORT,
    WorkflowStage.EXPORT: WorkflowStage.COMPLETE,
}


class WorkflowCoordinator:
    """Persist workflow transitions while task execution remains in TaskService."""

    def __init__(self, repository: ProjectRepository, *, global_auto_continue: bool):
        self.repository = repository
        project = repository.get_project()
        self.auto_continue = (
            project.workflow_auto_continue
            if project.workflow_auto_continue is not None
            else global_auto_continue
        )

    def begin(
        self,
        *,
        sequence_id: str,
        stage: WorkflowStage,
        asset_ids: list[str] | None = None,
        payload: dict | None = None,
        running: bool = False,
    ) -> WorkflowRun:
        project = self.repository.get_project()
        return self.repository.save_workflow_run(
            WorkflowRun(
                project_id=project.id,
                sequence_id=sequence_id,
                asset_ids=asset_ids or [],
                stage=stage,
                status=(WorkflowStatus.RUNNING if running else WorkflowStatus.AWAITING_CONFIRMATION),
                auto_continue=self.auto_continue,
                payload=payload or {},
                message_code=f"workflow_{stage.value}_ready",
            )
        )

    def mark_running(
        self,
        run_id: str,
        *,
        task_ids: list[str],
        payload: dict | None = None,
    ) -> WorkflowRun:
        run = self.repository.get_workflow_run(run_id)
        merged = {**run.payload, **(payload or {}), "task_ids": task_ids}
        return self.repository.save_workflow_run(
            run.model_copy(
                update={
                    "status": WorkflowStatus.RUNNING,
                    "payload": merged,
                    "message_code": f"workflow_{run.stage.value}_running",
                }
            )
        )

    def advance(
        self,
        run_id: str,
        *,
        asset_ids: list[str] | None = None,
        payload: dict | None = None,
    ) -> WorkflowRun:
        run = self.repository.get_workflow_run(run_id)
        next_stage = _NEXT_STAGE[run.stage]
        merged = {**run.payload, **(payload or {})}
        merged.pop("task_ids", None)
        if next_stage == WorkflowStage.COMPLETE:
            return self.repository.save_workflow_run(
                run.model_copy(
                    update={
                        "asset_ids": asset_ids if asset_ids is not None else run.asset_ids,
                        "stage": next_stage,
                        "status": WorkflowStatus.COMPLETED,
                        "payload": merged,
                        "message_code": "workflow_complete",
                    }
                )
            )
        return self.repository.save_workflow_run(
            run.model_copy(
                update={
                    "asset_ids": asset_ids if asset_ids is not None else run.asset_ids,
                    "stage": next_stage,
                    "status": WorkflowStatus.AWAITING_CONFIRMATION,
                    "payload": merged,
                    "message_code": f"workflow_{next_stage.value}_ready",
                }
            )
        )

    def block(self, run_id: str, message_code: str) -> WorkflowRun:
        run = self.repository.get_workflow_run(run_id)
        return self.repository.save_workflow_run(
            run.model_copy(
                update={
                    "status": WorkflowStatus.BLOCKED,
                    "message_code": message_code,
                }
            )
        )

    def await_confirmation(self, run_id: str) -> WorkflowRun:
        run = self.repository.get_workflow_run(run_id)
        return self.repository.save_workflow_run(
            run.model_copy(
                update={
                    "status": WorkflowStatus.AWAITING_CONFIRMATION,
                    "message_code": f"workflow_{run.stage.value}_ready",
                }
            )
        )

    def cancel(self, run_id: str) -> WorkflowRun:
        run = self.repository.get_workflow_run(run_id)
        return self.repository.save_workflow_run(
            run.model_copy(
                update={
                    "status": WorkflowStatus.CANCELLED,
                    "message_code": "workflow_cancelled",
                }
            )
        )
