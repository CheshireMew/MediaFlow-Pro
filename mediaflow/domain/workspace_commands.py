from __future__ import annotations

from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from mediaflow.domain.model_base import DomainModel

WorkspaceCommandName = Literal[
    "playhead.seek", "playback.play", "playback.pause", "playback.stop",
    "workspace.mode.activate", "timeline.selection.set",
]
WorkspaceMode = Literal["media", "resources", "transcript", "highlight", "audio", "tasks"]
WorkspaceSelectionId = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class FrameCommandArguments(DomainModel):
    frame: Annotated[int, Field(strict=True, ge=0)]


class EmptyCommandArguments(DomainModel):
    pass


class ModeCommandArguments(DomainModel):
    mode: WorkspaceMode


class SelectionCommandArguments(DomainModel):
    clip_ids: list[WorkspaceSelectionId] = Field(default_factory=list)
    transition_id: WorkspaceSelectionId | None = None

    @field_validator("clip_ids")
    @classmethod
    def unique_clip_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def one_selection_kind(self) -> Self:
        if self.clip_ids and self.transition_id:
            raise ValueError("Timeline selection cannot contain clips and a transition")
        return self


WORKSPACE_COMMANDS: MappingProxyType[str, type[DomainModel]] = MappingProxyType({
    "playhead.seek": FrameCommandArguments,
    "playback.play": FrameCommandArguments,
    "playback.pause": EmptyCommandArguments,
    "playback.stop": EmptyCommandArguments,
    "workspace.mode.activate": ModeCommandArguments,
    "timeline.selection.set": SelectionCommandArguments,
})


def validate_workspace_arguments(command: str, arguments: object) -> dict[str, JsonValue]:
    try:
        model = WORKSPACE_COMMANDS[command]
    except KeyError as error:
        raise ValueError(f"Unknown workspace command: {command}") from error
    return model.model_validate(arguments).model_dump(mode="json")


class WorkspaceCommandEvent(DomainModel):
    workspace_session_id: str
    workspace_revision: Annotated[int, Field(strict=True, ge=1)]
    project: str | None
    command: WorkspaceCommandName
    arguments: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_command(self) -> Self:
        normalized = validate_workspace_arguments(self.command, self.arguments)
        object.__setattr__(self, "arguments", normalized)
        return self
