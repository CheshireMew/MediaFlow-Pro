from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.enums import (
    ExportFormat,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.task_commands import SequenceBuildUnit, TaskCommand
from mediaflow.domain.tasks import Task

from .operation_model_common import SequenceArguments


class AudioBusChanges(DomainModel):
    name: str | None = None
    parent_bus_id: str | None = None
    position: int | None = None
    gain_db: float | None = None
    muted: bool | None = None
    solo: bool | None = None
    channel_layout: Literal["mono", "stereo", "5.1"] | None = None


class AudioBusUpdateArguments(DomainModel):
    bus_id: str = Field(min_length=1)
    changes: AudioBusChanges


class AudioEffectSaveArguments(DomainModel):
    effect: AudioEffect


class AudioEffectRemoveArguments(DomainModel):
    effect_id: str = Field(min_length=1)


class PreviewRenderArguments(SequenceArguments):
    use_proxies: bool | None = None


class PreviewFramesRenderArguments(PreviewRenderArguments):
    frames: list[int] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def valid_frames(self) -> PreviewFramesRenderArguments:
        if any(type(frame) is not int or frame < 0 for frame in self.frames):
            raise ValueError("frames must contain non-negative integers")
        if len(set(self.frames)) != len(self.frames):
            raise ValueError("frames must not contain duplicates")
        return self


class ExportSequenceArguments(SequenceArguments):
    output_path: str = Field(min_length=1)
    format: ExportFormat | None = None
    preset: ExportPreset | None = None
    overwrite: bool | None = None
    timeout: float | None = Field(default=None, gt=0)


class BuildSequenceArguments(SequenceArguments):
    units: list[SequenceBuildUnit] = Field(min_length=1)
    output_path: str = Field(min_length=1)
    format: ExportFormat | None = None
    preset: ExportPreset | None = None
    overwrite: bool | None = None
    timeout: float | None = Field(default=None, gt=0)


class ExportFcpxmlArguments(SequenceArguments):
    output_path: str = Field(min_length=1)
    overwrite: bool | None = None


class TaskStatusArguments(DomainModel):
    task_id: str = Field(min_length=1)


class TaskWaitArguments(TaskStatusArguments):
    timeout: float = Field(default=3600, gt=0, le=86_400)


class TaskStartArguments(SequenceArguments):
    task_command: TaskCommand
    input_asset_ids: list[str] | None = None
    timeout: float | None = Field(default=None, gt=0)


class TaskResumeArguments(DomainModel):
    task_id: str = Field(min_length=1)
    timeout: float | None = Field(default=None, gt=0)


class TaskReceiptResult(DomainModel):
    task: Task


class AudioBusWithEffects(AudioBus):
    effects: list[AudioEffect]


class AudioInspectResult(DomainModel):
    buses: list[AudioBusWithEffects]


class AudioBusResult(DomainModel):
    bus: AudioBus


class AudioEffectResult(DomainModel):
    effect: AudioEffect


class RemovedResult(DomainModel):
    removed: Literal[True]


class PreviewRenderResult(DomainModel):
    preview_graph: str


class PreviewProofFrame(DomainModel):
    frame: int = Field(ge=0)
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    byte_count: int = Field(gt=0)


class PreviewFramesRenderResult(DomainModel):
    content_revision: int = Field(ge=0)
    preview_graph: str
    frames: list[PreviewProofFrame]


class FcpxmlExportResult(DomainModel):
    format: Literal["fcpxml"] = "fcpxml"
    project_id: str
    sequence_id: str
    timeline_revision: int = Field(ge=0)
    output_path: str
    sha256: str = Field(pattern="^[a-f0-9]{64}$")


class TaskListResult(DomainModel):
    tasks: list[Task]


class TaskStatusResult(DomainModel):
    task: Task
