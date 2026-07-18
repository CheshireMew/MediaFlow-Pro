from pathlib import Path

from mediaflow.application.workflow_coordinator import WorkflowCoordinator
from mediaflow.domain.enums import WorkflowStage, WorkflowStatus
from mediaflow.domain.workflows import WorkflowPayload
from mediaflow.infrastructure.project_repository import ProjectRepository


def test_workflow_transitions_are_persisted_and_project_override_is_respected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Workflow"
    with ProjectRepository.create(root, "Workflow") as repository:
        project = repository.get_project()
        coordinator = WorkflowCoordinator(repository, global_auto_continue=False)
        run = coordinator.begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.DOWNLOAD,
            payload=WorkflowPayload(source_task_id="source-task"),
        )

        assert run.status == WorkflowStatus.AWAITING_CONFIRMATION
        assert run.auto_continue is False
        assert repository.get_workflow_run(run.id) == run

        running = coordinator.mark_running(run.id, task_ids=["task-1"])
        assert running.status == WorkflowStatus.RUNNING
        assert running.payload.task_ids == ["task-1"]

        prepared = coordinator.advance(run.id, asset_ids=[])
        assert prepared.stage == WorkflowStage.PREPARE_MEDIA
        assert prepared.status == WorkflowStatus.AWAITING_CONFIRMATION
        assert prepared.payload.task_ids == []

        repository.set_workflow_auto_continue(True)
        automatic = WorkflowCoordinator(repository, global_auto_continue=False).begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )
        assert automatic.auto_continue is True
        assert repository.list_workflow_runs(active_only=True)[0].id == automatic.id

        cancelled = coordinator.cancel(run.id)
        assert cancelled.status == WorkflowStatus.CANCELLED


def test_global_workflow_mode_is_used_only_when_project_inherits(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Inherited", "Inherited") as repository:
        project = repository.get_project()
        run = WorkflowCoordinator(repository, global_auto_continue=True).begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )
        assert run.auto_continue is True

        repository.set_workflow_auto_continue(False)
        overridden = WorkflowCoordinator(repository, global_auto_continue=True).begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )
        assert overridden.auto_continue is False
