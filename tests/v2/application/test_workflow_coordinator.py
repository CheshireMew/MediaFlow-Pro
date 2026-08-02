import threading
import time
from pathlib import Path

import pytest

from mediaflow.application.task_service import (
    TaskCompletion,
    TaskContext,
    TaskService,
)
from mediaflow.application.workflow_coordinator import WorkflowCoordinator
from mediaflow.composition import EditorApplication
from mediaflow.domain.enums import (
    AssetKind,
    TaskKind,
    TaskStatus,
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    WorkflowTaskLink,
)
from mediaflow.domain.workflows import WorkflowPayload
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.task_repository import TaskRepository


def test_workflow_transitions_are_persisted_and_project_override_is_respected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mediaflow.infrastructure.project_catalog_repository.now_ms",
        lambda: 1_000,
    )
    root = tmp_path / "Workflow"
    with ProjectRepository.create(root, "Workflow") as repository:
        project = repository.catalog.get_project()
        coordinator = WorkflowCoordinator(repository, global_auto_continue=False)
        run = coordinator.begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.DOWNLOAD,
            payload=WorkflowPayload(source_task_id="source-task"),
        )

        assert run.status == WorkflowStatus.AWAITING_CONFIRMATION
        assert run.auto_continue is False
        assert repository.catalog.get_workflow_run(run.id) == run

        running = coordinator.mark_running(run.id, task_ids=["task-1"])
        assert running.status == WorkflowStatus.RUNNING
        assert running.payload.task_ids == ["task-1"]

        prepared = coordinator.advance(run.id, asset_ids=[])
        assert prepared.stage == WorkflowStage.PREPARE_MEDIA
        assert prepared.status == WorkflowStatus.AWAITING_CONFIRMATION
        assert prepared.payload.task_ids == []

        repository.catalog.set_workflow_auto_continue(True)
        automatic = WorkflowCoordinator(repository, global_auto_continue=False).begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )
        assert automatic.auto_continue is True
        assert repository.catalog.list_workflow_runs(active_only=True)[0].id == automatic.id

        cancelled = coordinator.cancel(run.id)
        assert cancelled.status == WorkflowStatus.CANCELLED


def test_global_workflow_mode_is_used_only_when_project_inherits(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Inherited", "Inherited") as repository:
        project = repository.catalog.get_project()
        run = WorkflowCoordinator(repository, global_auto_continue=True).begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )
        assert run.auto_continue is True

        repository.catalog.set_workflow_auto_continue(False)
        overridden = WorkflowCoordinator(repository, global_auto_continue=True).begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )
        assert overridden.auto_continue is False


def test_project_workflow_skip_advances_every_optional_stage_and_stops_at_export(
    tmp_path: Path,
) -> None:
    application = EditorApplication()
    with application.create_project(tmp_path / "Skippable", "Skippable") as project:
        sequence_id = project.get_project().main_sequence_id
        run = project._workflows.coordinator.begin(
            sequence_id=sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )

        for skipped_stage, expected_stage in (
            (WorkflowStage.PREPARE_MEDIA, WorkflowStage.TRANSCRIBE),
            (WorkflowStage.TRANSCRIBE, WorkflowStage.TRANSLATE),
            (WorkflowStage.TRANSLATE, WorkflowStage.HIGHLIGHT),
            (WorkflowStage.HIGHLIGHT, WorkflowStage.CREATE_SHORTS),
            (WorkflowStage.CREATE_SHORTS, WorkflowStage.EXPORT),
        ):
            assert project.active_workflow().stage == skipped_stage
            update = project.skip_workflow(run.id)
            assert "已跳过工作流阶段" in update.status_message
            assert project.active_workflow().stage == expected_stage
            assert project.active_workflow().status == WorkflowStatus.AWAITING_CONFIRMATION

        with pytest.raises(ValueError, match="不能跳过"):
            project.skip_workflow(run.id)


def test_failed_task_blocks_workflow_without_consuming_success_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MEDIAFLOW_RUNTIME_DIR",
        str(tmp_path / "runtime"),
    )
    application = EditorApplication()
    with application.create_project(tmp_path / "Failure", "Failure") as project:
        started = threading.Event()
        release = threading.Event()

        def fail(_context: TaskContext) -> TaskCompletion:
            started.set()
            assert release.wait(5)
            raise RuntimeError("observable workflow failure")

        project._tasks._handlers[TaskKind.ANALYZE] = fail
        run = project._workflows.coordinator.begin(
            sequence_id=project.get_project().main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )
        task = project.start_task(
            AnalyzeDownloadCommand(
                url="test://workflow-failure",
                workflow=WorkflowTaskLink(
                    run_id=run.id,
                    stage=run.stage,
                ),
            )
        )
        assert started.wait(5)
        project._workflows.coordinator.mark_running(
            run.id,
            task_ids=[task.id],
        )
        release.set()

        failed = project.wait_for_task(task.id, timeout=5)
        assert failed.status == TaskStatus.FAILED
        deadline = time.monotonic() + 5
        while (
            project._repository.catalog.get_workflow_run(run.id).status
            != WorkflowStatus.BLOCKED
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        persisted = project._repository.catalog.get_workflow_run(run.id)
        assert persisted.status == WorkflowStatus.BLOCKED
        assert persisted.message_code == "workflow_task_failed"


def test_cancelled_task_blocks_workflow_for_recovery_without_consuming_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MEDIAFLOW_RUNTIME_DIR",
        str(tmp_path / "runtime"),
    )
    application = EditorApplication()
    with application.create_project(
        tmp_path / "Cancellation",
        "Cancellation",
    ) as project:
        started = threading.Event()

        def wait_for_cancel(context: TaskContext) -> TaskCompletion:
            started.set()
            while True:
                context.cancellation.raise_if_requested()
                time.sleep(0.01)

        project._tasks._handlers[TaskKind.ANALYZE] = wait_for_cancel
        run = project._workflows.coordinator.begin(
            sequence_id=project.get_project().main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
        )
        task = project.start_task(
            AnalyzeDownloadCommand(
                url="test://workflow-cancel",
                workflow=WorkflowTaskLink(
                    run_id=run.id,
                    stage=run.stage,
                ),
            )
        )
        assert started.wait(5)
        project._workflows.coordinator.mark_running(
            run.id,
            task_ids=[task.id],
        )
        project.cancel_task(task.id)

        cancelled = project.wait_for_task(task.id, timeout=5)
        assert cancelled.status == TaskStatus.CANCELLED
        deadline = time.monotonic() + 5
        while (
            project._repository.catalog.get_workflow_run(run.id).status
            != WorkflowStatus.BLOCKED
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        persisted = project._repository.catalog.get_workflow_run(run.id)
        assert persisted.status == WorkflowStatus.BLOCKED
        assert persisted.message_code == "workflow_task_cancelled"


def test_reopen_settles_workflow_when_owner_crashed_after_persisting_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MEDIAFLOW_RUNTIME_DIR",
        str(tmp_path / "runtime"),
    )
    root = tmp_path / "FailureBeforeWorkflowReceipt"
    repository = ProjectRepository.create(
        root,
        "FailureBeforeWorkflowReceipt",
    )
    project_record = repository.catalog.get_project()
    coordinator = WorkflowCoordinator(
        repository,
        global_auto_continue=False,
    )
    run = coordinator.begin(
        sequence_id=project_record.main_sequence_id,
        stage=WorkflowStage.PREPARE_MEDIA,
    )
    tasks = TaskService(
        TaskRepository(repository),
        max_workers=1,
        recover_expired=False,
    )
    started = threading.Event()
    release = threading.Event()

    def fail(_context: TaskContext) -> TaskCompletion:
        started.set()
        assert release.wait(5)
        raise RuntimeError("persisted before process crash")

    try:
        tasks.register(TaskKind.ANALYZE, fail)
        task = tasks.start(
            project_id=project_record.id,
            sequence_id=project_record.main_sequence_id,
            command=AnalyzeDownloadCommand(
                url="test://failure-before-workflow-receipt",
                workflow=WorkflowTaskLink(
                    run_id=run.id,
                    stage=run.stage,
                ),
            ),
        )
        assert started.wait(5)
        coordinator.mark_running(run.id, task_ids=[task.id])
        release.set()
        assert tasks.wait(task.id, timeout=5).status == TaskStatus.FAILED
        assert (
            repository.catalog.get_workflow_run(run.id).status
            == WorkflowStatus.RUNNING
        )
    finally:
        release.set()
        tasks.shutdown()
        repository.close()

    application = EditorApplication()
    with application.open_project(root, writable=True) as reopened:
        settled = reopened._repository.catalog.get_workflow_run(run.id)
        assert settled.status == WorkflowStatus.BLOCKED
        assert settled.message_code == "workflow_task_failed"


def test_failed_workflow_recovery_creates_a_new_persisted_stage_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MEDIAFLOW_RUNTIME_DIR",
        str(tmp_path / "runtime"),
    )
    source = tmp_path / "high-resolution.mp4"
    source.write_bytes(b"workflow retry source")
    application = EditorApplication()
    with application.create_project(
        tmp_path / "RetryAttempt",
        "RetryAttempt",
    ) as project:
        project.set_workflow_mode(False)
        asset = project._repository.catalog.import_external_asset(
            source,
            AssetKind.VIDEO,
        )
        asset = project._repository.catalog.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={"width": 3840, "height": 2160}
                    )
                }
            )
        )
        run = project._workflows.coordinator.begin(
            sequence_id=project.get_project().main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
            asset_ids=[asset.id],
        )
        attempts = 0
        second_started = threading.Event()
        release_second = threading.Event()

        def proxy(_context: TaskContext) -> TaskCompletion:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("first proxy attempt failed")
            second_started.set()
            assert release_second.wait(5)
            return TaskCompletion()

        project._tasks._handlers[TaskKind.PROXY] = proxy
        project.continue_workflow(run.id)
        deadline = time.monotonic() + 5
        while (
            project._repository.catalog.get_workflow_run(run.id).status
            != WorkflowStatus.BLOCKED
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        failed_run = project._repository.catalog.get_workflow_run(run.id)
        assert failed_run.status == WorkflowStatus.BLOCKED
        assert failed_run.payload.stage_attempt == 1
        first_task = project.get_task(failed_run.payload.task_ids[0])
        assert first_task.status == TaskStatus.FAILED
        assert first_task.idempotency_key == (
            f"workflow:{run.id}:prepare_media:1:0"
        )

        project.continue_workflow(run.id)
        assert second_started.wait(5)
        retried_run = project._repository.catalog.get_workflow_run(run.id)
        second_task = project.get_task(retried_run.payload.task_ids[0])
        assert retried_run.payload.stage_attempt == 2
        assert second_task.id != first_task.id
        assert second_task.idempotency_key == (
            f"workflow:{run.id}:prepare_media:2:0"
        )

        release_second.set()
        assert (
            project.wait_for_task(second_task.id, timeout=5).status
            == TaskStatus.COMPLETED
        )
        deadline = time.monotonic() + 5
        while (
            project._repository.catalog.get_workflow_run(run.id).stage
            != WorkflowStage.TRANSCRIBE
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        advanced = project._repository.catalog.get_workflow_run(run.id)
        assert advanced.stage == WorkflowStage.TRANSCRIBE
        assert advanced.status == WorkflowStatus.AWAITING_CONFIRMATION
        assert attempts == 2
