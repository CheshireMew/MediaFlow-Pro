from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from mediaflow.application.project_mutation_planning import plan_automation_project_mutation
from mediaflow.automation.contracts import AutomationRequest
from mediaflow.automation.operation_context import OperationContext
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.task_commands import AnalyzeDownloadCommand, DownloadMediaCommand
from mediaflow.domain.tasks import Task


class RecordingProject:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def start_task(self, command: Any, **kwargs: Any) -> Task:
        self.calls.append((command, kwargs))
        return Task(project_id="download-test", command=command, **kwargs)


def invoke(operation: str, arguments: dict[str, Any]) -> tuple[Task, RecordingProject]:
    definition = OPERATIONS[operation]
    arguments = definition.validate_arguments({"sequence_id": "main", **arguments})
    project = RecordingProject()
    envelope = AutomationRequest(
        operation=operation,
        project="download-test",
        request_id="stable-download-request",
        actor=ActorIdentity(kind="agent", id="download-test"),
        client_id="pytest",
        arguments=arguments,
    )
    result = definition.handler(OperationContext(cast(Any, project), None, envelope))
    definition.validate_result(result)
    return Task.model_validate(result["task"]), project


def test_download_analysis_uses_persisted_task_and_request_identity() -> None:
    task, project = invoke("download.analyze", {"url": " https://example.com/video "})
    command, options = project.calls[0]
    assert isinstance(command, AnalyzeDownloadCommand)
    assert command.url == "https://example.com/video"
    assert options["idempotency_key"] == "automation:stable-download-request:download.analyze"
    assert task.sequence_id == "main"


def test_download_start_preserves_exact_entry_and_output_destination(tmp_path: Path) -> None:
    request = {
        "entry": {
            "index": 2,
            "title": "Selected video",
            "page_url": "https://example.com/playlist",
            "download_url": "https://example.com/video-2",
            "selector": 2,
        },
        "output_directory": str(tmp_path),
        "resolution": "720p",
        "download_subtitles": False,
    }
    task, project = invoke("download.start", {"request": request})
    command, options = project.calls[0]
    assert isinstance(command, DownloadMediaCommand)
    assert command.request.entry.selector == 2
    assert command.request.output_directory == str(tmp_path.resolve())
    assert command.request.resolution == "720p"
    assert options["idempotency_key"] == "automation:stable-download-request:download.start"
    assert task.command == command


def test_download_contract_rejects_missing_target_and_unavailable_entry(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        OPERATIONS["download.analyze"].validate_arguments({"url": " "})
    with pytest.raises(ValidationError):
        OPERATIONS["download.start"].validate_arguments({"request": {
            "entry": {"index": 1, "title": "Unavailable", "available": False,
                      "unavailable_reason": "Removed"},
            "output_directory": str(tmp_path),
        }})


@pytest.mark.parametrize("operation", ["download.analyze", "download.start"])
def test_download_operations_declare_task_execution_and_mutation_scope(operation: str) -> None:
    definition = OPERATIONS[operation]
    assert definition.project_access == "write"
    assert definition.execution_mode == "task"
    assert definition.idempotency == "optional"
    plan = plan_automation_project_mutation(operation, {}, default_sequence_id="main")
    assert plan.change_scopes == [f"/tasks/{operation.replace('.', '-')}:main"]
