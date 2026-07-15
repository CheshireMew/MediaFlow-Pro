from pathlib import Path

from mediaflow.application.task_service import TaskContext, TaskService
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.models import Task
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
        context.report_progress(25, "writing")
        output.write_text("observable producer output", encoding="utf-8")
        context.report_progress(75, "verifying")
        assert output.read_text(encoding="utf-8") == "observable producer output"
        return [str(output.relative_to(context.project_dir).as_posix())]

    service.register(TaskKind.ANALYZE, generate)
    started = service.start(project_id=project.id, kind=TaskKind.ANALYZE, name="Generate")
    completed = service.wait(started.id, timeout=5)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.artifacts == ["generated/task-output.txt"]
    assert (root / completed.artifacts[0]).read_text(encoding="utf-8") == "observable producer output"
    assert task_repository.get(started.id).status == TaskStatus.COMPLETED
    assert [event.event_type for event in events][-1] == "completed"

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
            kind=TaskKind.EXPORT,
            status=TaskStatus.RUNNING,
            name="Interrupted export",
        )
    )

    service = TaskService(repository, max_workers=1)
    recovered = repository.get(running.id)
    assert recovered.status == TaskStatus.PAUSED
    assert recovered.message_code == "interrupted_by_restart"

    service.shutdown()
    project_repository.close()
