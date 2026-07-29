from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.domain.task_commands import CommandModel
from mediaflow.domain.tasks import ArtifactReference, TaskOutcome

CommandT = TypeVar("CommandT", bound=CommandModel)


class ProjectTaskHandler:
    """Shared task-envelope mechanics; domain handlers own the actual work."""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir

    def completion(
        self,
        *values: str | Path | None,
        outcome: TaskOutcome | None = None,
    ) -> TaskCompletion:
        return TaskCompletion(
            artifacts=tuple(
                ArtifactReference.from_path(self.project_dir, value) for value in values if value
            ),
            outcome=outcome,
        )

    @staticmethod
    def command(context: TaskContext, expected: type[CommandT]) -> CommandT:
        command = context.task.command
        if not isinstance(command, expected):
            raise TypeError(f"Unexpected {context.task.kind.value} command: {type(command).__name__}")
        return command
