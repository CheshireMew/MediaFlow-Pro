import time
from pathlib import Path

from mediaflow.application.task_service import TaskContext, TaskService
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.task_commands import AnalyzeDownloadCommand, ExportSequenceCommand
from mediaflow.domain.tasks import Task
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.task_repository import TaskRepository


def test_real_task_generates_artifact_and_consumer_reads_persisted_result(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.get_project()
    task_repository = TaskRepository(root)
    service = TaskService(task_repository, max_workers=1)
    events = []
    service.events.subscribe(events.append)

    def generate(context: TaskContext) -> list[str]:
        output = context.project_dir / "generated" / "task-output.txt"
        payload = b"observable producer output"
        context.report(
            OperationProgress.determinate(
                "writing",
                completed=0,
                total=len(payload),
                unit="bytes",
            )
        )
        output.write_bytes(payload)
        context.report(
            OperationProgress.determinate(
                "writing",
                completed=output.stat().st_size,
                total=len(payload),
                unit="bytes",
            )
        )
        context.report(OperationProgress.indeterminate("verifying"))
        assert output.read_text(encoding="utf-8") == "observable producer output"
        return [str(output.relative_to(context.project_dir).as_posix())]

    service.register(TaskKind.ANALYZE, generate)
    started = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://generate"),
    )
    completed = service.wait(started.id, timeout=5)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.artifacts == ["generated/task-output.txt"]
    assert (root / completed.artifacts[0]).read_text(encoding="utf-8") == "observable producer output"
    assert task_repository.get(started.id).status == TaskStatus.COMPLETED
    assert [(item.step, item.status) for item in completed.execution_trace] == [
        ("writing", "success"),
        ("verifying", "success"),
    ]
    assert [event.event_type for event in events][-1] == "completed"
    event_tasks = [Task.model_validate(event.payload) for event in events]
    assert "kind" not in events[-1].payload
    assert event_tasks[-1] == completed

    service.shutdown()
    project_repository.close()


def test_running_task_is_paused_after_process_restart(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.get_project()
    repository = TaskRepository(root)
    running = repository.create(
        Task(
            project_id=project.id,
            command=ExportSequenceCommand(
                sequence_id=project.main_sequence_id,
                output_path=str(root / "exports" / "interrupted.mp4"),
            ),
            status=TaskStatus.RUNNING,
        )
    )

    service = TaskService(repository, max_workers=1)
    recovered = repository.get(running.id)
    assert recovered.status == TaskStatus.PAUSED
    assert recovered.progress.message_code == "interrupted_by_restart"

    service.shutdown()
    project_repository.close()


def test_failed_task_can_retry_and_history_removal_keeps_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.get_project()
    repository = TaskRepository(root)
    service = TaskService(repository, max_workers=1)
    attempts = 0

    def generate(context: TaskContext) -> list[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("first attempt failed")
        output = context.project_dir / "generated" / "retry-output.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("retry completed", encoding="utf-8")
        return [str(output.relative_to(context.project_dir).as_posix())]

    service.register(TaskKind.ANALYZE, generate)
    original = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://retry"),
    )
    assert service.wait(original.id, timeout=5).status == TaskStatus.FAILED

    retried = service.retry(original.id)
    completed = service.wait(retried.id, timeout=5)
    artifact = root / completed.artifacts[0]
    assert retried.id != original.id
    assert completed.status == TaskStatus.COMPLETED
    assert repository.get(original.id).status == TaskStatus.FAILED
    assert artifact.read_text(encoding="utf-8") == "retry completed"

    service.delete(original.id)
    try:
        repository.get(original.id)
    except KeyError:
        pass
    else:
        raise AssertionError("removed task history is still persisted")
    assert service.clear_history() == 1
    assert artifact.read_text(encoding="utf-8") == "retry completed"
    assert repository.list() == []

    service.shutdown()
    project_repository.close()


def test_bulk_pause_resume_and_cancel_control_the_real_queue(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.get_project()
    repository = TaskRepository(root)
    service = TaskService(repository, max_workers=1)

    def wait_for_control(context: TaskContext) -> list[str]:
        while True:
            context.cancellation.raise_if_requested()
            time.sleep(0.005)

    service.register(TaskKind.ANALYZE, wait_for_control)
    tasks = [
        service.start(
            project_id=project.id,
            command=AnalyzeDownloadCommand(url=f"test://queue/{index}"),
        )
        for index in range(2)
    ]
    deadline = time.monotonic() + 2
    while (
        not any(service.get(task.id).status == TaskStatus.RUNNING for task in tasks)
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert service.pause_all() == 2
    assert [service.wait(task.id, timeout=5).status for task in tasks] == [
        TaskStatus.PAUSED,
        TaskStatus.PAUSED,
    ]

    for task in tasks:
        service.resume(task.id)
    assert service.cancel_all() == 2
    assert [service.wait(task.id, timeout=5).status for task in tasks] == [
        TaskStatus.CANCELLED,
        TaskStatus.CANCELLED,
    ]

    service.shutdown()
    project_repository.close()


def test_observer_failure_cannot_rewrite_a_completed_task(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.get_project()
    service = TaskService(TaskRepository(root), max_workers=1)

    def fail_observer(_event) -> None:
        raise RuntimeError("observer failure")

    service.events.subscribe(fail_observer, include_snapshot=False)
    service.register(TaskKind.ANALYZE, lambda _context: ["generated/result.json"])

    started = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://observe"),
    )
    completed = service.wait(started.id, timeout=5)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.error is None
    assert completed.artifacts == ["generated/result.json"]
    service.shutdown()
    project_repository.close()


def test_progress_updates_are_throttled_and_finished_future_is_released(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    project_repository = ProjectRepository.create(root, "Project")
    project = project_repository.get_project()
    service = TaskService(TaskRepository(root), max_workers=1)
    events = []
    service.events.subscribe(events.append, include_snapshot=False)

    def noisy(context: TaskContext) -> list[str]:
        for value in range(1000):
            context.report(
                OperationProgress.determinate(
                    "working",
                    completed=value,
                    total=1000,
                    unit="items",
                )
            )
        return []

    service.register(TaskKind.ANALYZE, noisy)
    started = service.start(
        project_id=project.id,
        command=AnalyzeDownloadCommand(url="test://noisy"),
    )
    assert service.wait(started.id, timeout=5).status == TaskStatus.COMPLETED
    deadline = time.monotonic() + 1
    while started.id in service._futures and time.monotonic() < deadline:
        time.sleep(0.001)

    progress_events = [event for event in events if event.event_type == "progress"]
    assert len(progress_events) < 150
    assert started.id not in service._futures
    service.shutdown()
    project_repository.close()
