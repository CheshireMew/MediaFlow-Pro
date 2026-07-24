from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field

from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.model_base import DomainModel, new_id, now_ms
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.task_commands import TaskCommand


class TaskExecutionTraceItem(DomainModel):
    step: str
    status: Literal["running", "success", "failed", "cancelled"] = "running"
    started_at: int = Field(default_factory=now_ms)
    duration_ms: int = 0
    error: str | None = None


class Task(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    sequence_id: str | None = None
    command: TaskCommand
    status: TaskStatus = TaskStatus.PENDING
    progress: OperationProgress = Field(
        default_factory=lambda: OperationProgress.indeterminate("queued")
    )
    input_asset_ids: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    execution_trace: list[TaskExecutionTraceItem] = Field(default_factory=list)
    error: str | None = None
    revision: int = 0
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)

    @computed_field
    @property
    def kind(self) -> TaskKind:
        return self.command.task_kind
