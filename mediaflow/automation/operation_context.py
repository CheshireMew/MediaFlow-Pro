from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from mediaflow.domain.tasks import Task
from mediaflow.domain.web_media import web_asset_spec_document

if TYPE_CHECKING:
    from mediaflow.automation.contracts import AutomationRequest
    from mediaflow.composition import EditorApplication, EditorProject


@dataclass(frozen=True, slots=True)
class OperationContext:
    _project: EditorProject | None
    _application: EditorApplication | None
    envelope: AutomationRequest
    retrying: bool = False

    @property
    def project(self) -> EditorProject:
        if self._project is None:
            raise RuntimeError(
                f"{self.envelope.operation} requires an open project"
            )
        return self._project

    @property
    def application(self) -> EditorApplication:
        if self._application is None:
            raise RuntimeError(
                f"{self.envelope.operation} requires the application runtime"
            )
        return self._application

    @property
    def application_or_none(self) -> EditorApplication | None:
        return self._application

    @property
    def arguments(self) -> dict[str, Any]:
        return self.envelope.arguments

    def required(self, name: str) -> Any:
        value = self.arguments.get(name)
        if value is None or value == "":
            raise ValueError(f"arguments.{name} is required")
        return value

    def sequence_id(self) -> str:
        return str(
            self.arguments.get("sequence_id")
            or self.project.get_project().main_sequence_id
        )

    def actor(self) -> Literal["human", "automation"]:
        return cast(
            Literal["human", "automation"],
            str(self.arguments.get("actor", "automation")),
        )

    def task_idempotency(self) -> str | None:
        if not self.envelope.request_id:
            return None
        return (
            f"automation:{self.envelope.request_id}:"
            f"{self.envelope.operation}"
        )

    def task_result(self, task: Task) -> dict[str, Any]:
        completed = self.project.wait_for_task(
            task.id,
            timeout=float(self.arguments.get("timeout", 3600)),
        )
        result = self.project.consume_task_result(completed)
        return {
            "task": completed.model_dump(
                mode="json",
                exclude_computed_fields=True,
            ),
            "result": result.as_dict(),
        }


def project_snapshot(project: EditorProject) -> dict[str, Any]:
    return {
        "project": project.get_project(),
        "path": str(project.project_dir),
        "read_only": project.read_only,
        "sequences": project.list_sequences(),
        "assets": project.list_assets(),
        "web_assets": [
            web_asset_spec_document(item)
            for item in project.list_web_assets()
        ],
        "active_workflows": project.list_workflow_runs(active_only=True),
        "tasks": project.list_tasks(),
    }
