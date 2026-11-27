from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaflow.composition import EditorProject
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.settings import ServiceSettings
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.task_runtime import (
    InfrastructureExportTaskRuntime,
    InfrastructureWebTaskRuntime,
)
from mediaflow.service.codec import encode_transport
from mediaflow.service.desktop_sessions import DesktopProjectOperations


@pytest.mark.parametrize("command", ["prepare_web_sequence", "export_fcpxml"])
def test_long_web_command_is_a_cancellable_task_without_blocking_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Runtime", "Runtime")
    project = EditorProject(repository, settings=ServiceSettings(), paths=RuntimeContext.discover().paths)
    entered = threading.Event()
    release = threading.Event()
    errors = []

    @contextmanager
    def leased(*args, **kwargs):
        yield SimpleNamespace(project=project)

    @contextmanager
    def locked(*args, **kwargs):
        with project.write_gate:
            yield SimpleNamespace(project=project)

    operations = DesktopProjectOperations(SimpleNamespace(leased_session=leased, locked_session=locked))

    def prepare(self, state, *, progress, check_cancelled):
        entered.set()
        while not release.wait(0.01):
            check_cancelled()
        return ()

    monkeypatch.setattr(InfrastructureWebTaskRuntime, "prepare_sequence", prepare)
    monkeypatch.setattr(
        InfrastructureExportTaskRuntime,
        "export_fcpxml",
        (
            lambda self, state, output, *, overwrite, progress, check_cancelled: prepare(
                self, state, progress=progress, check_cancelled=check_cancelled
            )
        ),
    )
    # This regression holds the long render stage; output preflight has its
    # own real-file contract coverage in the web integration tests.
    if command == "export_fcpxml":
        monkeypatch.setattr(
            "mediaflow.infrastructure.fcpxml_export.FcpxmlExportService.preflight",
            lambda self, state, output, *, overwrite: Path(output),
        )
    state = project.timeline(project.get_project().main_sequence_id).state

    def call(command, arguments):
        return operations.execute_desktop_command(
            path=project.project_dir,
            target="project",
            sequence_id="",
            command=command,
            arguments_value=encode_transport(arguments),
            base_revision=project.content_revision(),
            request_id="runtime",
            actor_value={"kind": "human", "id": "desktop"},
        )

    def render():
        try:
            arguments = (
                {"state": state}
                if command == "prepare_web_sequence"
                else {"sequence_id": state.sequence.id, "destination": str(tmp_path / "output.fcpxml")}
            )
            call(command, arguments)
        except RuntimeError as error:
            errors.append(str(error))

    worker = threading.Thread(target=render, daemon=True)
    try:
        worker.start()
        assert entered.wait(3)
        # The running long command must not hold the mutation gate.
        edited = threading.Event()

        def edit():
            repository.projects.rename_project("Edited while rendering")
            edited.set()

        editor = threading.Thread(target=edit, daemon=True)
        editor.start()
        assert edited.wait(2)
        editor.join(2)
        call("pause_all_tasks", {})
        worker.join(3)
        assert not worker.is_alive()
        tasks = project.list_tasks()
        assert len(tasks) == 1 and tasks[0].status == TaskStatus.PAUSED
        assert errors == ["网页序列渲染已停止" if command == "prepare_web_sequence" else "FCPXML 导出已停止"]
        assert project.get_project().name == "Edited while rendering"
    finally:
        release.set()
        worker.join(3)
        project.close(timeout=5)
