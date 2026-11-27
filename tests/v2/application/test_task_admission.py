from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mediaflow.application.task_service import TaskCompletion, TaskContext, TaskService
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.task_commands import AnalyzeDownloadCommand, GenerateProxyCommand
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.task_repository import TaskRepository


@pytest.mark.parametrize("clear_all", [False, True])
def test_history_removal_during_completion_does_not_leak_worker_capacity(
    tmp_path: Path, clear_all: bool
) -> None:
    with ProjectRepository.create(tmp_path / "History", "History") as project:
        service = TaskService(TaskRepository(project), max_workers=1, recover_expired=False)
        removed = threading.Event()

        def remove_on_completion(event) -> None:
            if event.event_type == "completed" and not removed.is_set():
                if clear_all:
                    service.clear_history()
                else:
                    service.delete(event.task_id)
                removed.set()

        service.events.subscribe(remove_on_completion)
        service.register(TaskKind.ANALYZE, lambda _context: TaskCompletion())
        try:
            project_id = project.projects.get_project().id
            service.start(project_id=project_id, command=AnalyzeDownloadCommand(url="test://first"))
            assert removed.wait(2)
            # Wait for the worker callback stack to return before publishing
            # another created event from this thread.
            service._execution.executor.submit(lambda: None).result(timeout=2)
            tail = service.start(project_id=project_id, command=AnalyzeDownloadCommand(url="test://tail"))
            assert service.wait(tail.id, 2).status == TaskStatus.COMPLETED
        finally:
            service.shutdown(timeout=5)


def test_paused_task_waiting_for_capacity_resumes_in_same_service(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Admission", "Admission") as project:
        project_id = project.projects.get_project().id
        repository = TaskRepository(project)
        service = TaskService(repository, max_workers=1, recovery_poll_interval=0.02)
        entered = threading.Event()
        release = threading.Event()

        def hold(context: TaskContext) -> TaskCompletion:
            entered.set()
            while not release.wait(0.01):
                context.cancellation.raise_if_requested()
            return TaskCompletion()

        service.register(TaskKind.ANALYZE, hold)
        service.register(TaskKind.PROXY, lambda _context: TaskCompletion())
        try:
            first = service.start(project_id=project_id, command=AnalyzeDownloadCommand(url="test://hold"))
            assert entered.wait(2)
            waiting = service.start(project_id=project_id, command=GenerateProxyCommand(asset_id="asset"))
            service.pause(waiting.id)
            assert service.get(waiting.id).status == TaskStatus.PAUSED
            service.resume(waiting.id)
            release.set()
            assert service.wait(first.id, 3).status == TaskStatus.COMPLETED
            assert service.wait(waiting.id, 3).status == TaskStatus.COMPLETED
        finally:
            release.set()
            service.shutdown(timeout=5)


def test_durable_backlog_does_not_create_unbounded_executor_objects(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Backlog", "Backlog") as project:
        project_id = project.projects.get_project().id
        repository = TaskRepository(project)
        service = TaskService(repository, max_workers=1, recovery_poll_interval=0.02)
        entered = threading.Event()
        release = threading.Event()

        def hold(context: TaskContext) -> TaskCompletion:
            entered.set()
            while not release.wait(0.01):
                context.cancellation.raise_if_requested()
            return TaskCompletion()

        service.register(TaskKind.ANALYZE, hold)
        try:
            tasks = [service.start(project_id=project_id, command=AnalyzeDownloadCommand(url="test://0"))]
            assert entered.wait(2)
            for index in range(1, 24):
                tasks.append(
                    service.start(
                        project_id=project_id,
                        command=AnalyzeDownloadCommand(url=f"test://{index}"),
                    )
                )
            with service._execution.lock:
                assert len(service._execution.tokens) <= 1
                assert len(service._execution.futures) <= 1
            assert sum(task.status == TaskStatus.PENDING for task in repository.list()) == 23
            service.cancel(tasks[-1].id)
            release.set()
            for task in tasks[:-1]:
                assert service.wait(task.id, 5).status == TaskStatus.COMPLETED
            assert service.get(tasks[-1].id).status == TaskStatus.CANCELLED
        finally:
            release.set()
            service.shutdown(timeout=5)


def test_recovery_reads_bounded_eligible_batches_and_excludes_active_ids(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Selection", "Selection") as project:
        repository = TaskRepository(project)
        service = TaskService(repository, max_workers=1, recover_expired=False)
        project_id = project.projects.get_project().id
        try:
            # No handlers are registered yet, so all accepted candidates stay
            # in durable storage rather than being claimed by recovery.
            from mediaflow.domain.tasks import Task

            tasks = [
                repository.create(
                    Task(
                        project_id=project_id,
                        command=GenerateProxyCommand(asset_id=f"asset-{index}"),
                    )
                )
                for index in range(12)
            ]
            light = repository.create(
                Task(
                    project_id=project_id,
                    command=AnalyzeDownloadCommand(url="test://light"),
                )
            )
            selected = repository.list_claimable(0, limit=2, kinds=(TaskKind.PROXY,))
            assert len(selected) == 2
            excluded = tuple(task.id for task in selected)
            second = repository.list_claimable(0, limit=2, kinds=(TaskKind.PROXY,), exclude_ids=excluded)
            assert len(second) == 2
            assert not set(excluded) & {task.id for task in second}
            assert repository.list_claimable(0, limit=1, kinds=(TaskKind.ANALYZE,))[0].id == light.id
            assert len(repository.list_claimable(0, limit=3)) == 3
            assert len(repository.list()) == len(tasks) + 1
        finally:
            service.shutdown(timeout=5)


def test_pending_tail_survives_service_shutdown_and_drains_after_reopen(tmp_path: Path) -> None:
    root = tmp_path / "Restart"
    with ProjectRepository.create(root, "Restart") as project:
        service = TaskService(TaskRepository(project), max_workers=1, recovery_poll_interval=0.02)
        project_id = project.projects.get_project().id
        entered = threading.Event()

        def hold(context: TaskContext) -> TaskCompletion:
            entered.set()
            while True:
                context.cancellation.raise_if_requested()
                threading.Event().wait(0.01)

        service.register(TaskKind.ANALYZE, hold)
        first = service.start(project_id=project_id, command=AnalyzeDownloadCommand(url="test://hold"))
        assert entered.wait(2)
        pending = [
            service.start(
                project_id=project_id,
                command=AnalyzeDownloadCommand(url=f"test://tail-{index}"),
            )
            for index in range(7)
        ]
        service.shutdown(timeout=5)
        assert service.get(first.id).status == TaskStatus.PAUSED
        assert all(service.get(task.id).status == TaskStatus.PENDING for task in pending)
    with ProjectRepository.open(root) as project:
        recovered = TaskService(TaskRepository(project), max_workers=2, recovery_poll_interval=0.02)
        recovered.register(TaskKind.ANALYZE, lambda _context: TaskCompletion())
        try:
            for task in pending:
                assert recovered.wait(task.id, 5).status == TaskStatus.COMPLETED
            recovered.resume(first.id)
            assert recovered.wait(first.id, 5).status == TaskStatus.COMPLETED
        finally:
            recovered.shutdown(timeout=5)
