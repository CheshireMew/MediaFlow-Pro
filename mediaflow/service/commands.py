from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from pydantic import JsonValue

from mediaflow.application.desktop_mutation_adapter import (
    ProjectMutationDocuments,
    plan_desktop_project_mutation,
)
from mediaflow.domain.collaboration import ProjectMutationPlan
from mediaflow.domain.model_base import DomainModel
from mediaflow.service.desktop_command_catalog import DESKTOP_COMMAND_GROUPS

CommandAccess = Literal["read", "write", "runtime"]
HistoryMode = Literal["reversible", "non_undoable"]
DesktopTarget = Literal["project", "timeline"]


class DesktopCommandRequest(DomainModel):
    args: JsonValue
    kwargs: JsonValue


class DesktopCommandResult(DomainModel):
    value: JsonValue


@dataclass(frozen=True, slots=True)
class DesktopCommand:
    target: DesktopTarget
    name: str
    access: CommandAccess
    history_mode: HistoryMode = "non_undoable"
    request_model: type[DesktopCommandRequest] = DesktopCommandRequest
    result_model: type[DesktopCommandResult] = DesktopCommandResult

    @property
    def schema_id(self) -> str:
        return f"desktop.{self.target}.{self.name}.v1"

    def validate_request(self, args: object, kwargs: object) -> DesktopCommandRequest:
        return self.request_model.model_validate({"args": args, "kwargs": kwargs})

    def validate_result(self, value: object) -> JsonValue:
        return self.result_model.model_validate({"value": value}).value

    def invoke(
        self,
        receiver: object,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Any:
        member = getattr(receiver, self.name)
        return member(*args, **kwargs) if callable(member) else member

    def mutation_plan(
        self,
        *,
        sequence_id: str,
        args: list[Any],
        kwargs: dict[str, Any],
        project: ProjectMutationDocuments | None = None,
    ) -> ProjectMutationPlan:
        return plan_desktop_project_mutation(
            self.target,
            self.name,
            sequence_id=sequence_id,
            args=args,
            kwargs=kwargs,
            project=project,
        )


def _register_commands(
    registry: dict[tuple[DesktopTarget, str], DesktopCommand],
    *,
    target: DesktopTarget,
    access: CommandAccess,
    names: tuple[str, ...],
    reversible: frozenset[str] = frozenset(),
    all_reversible: bool = False,
) -> None:
    for name in names:
        key = (target, name)
        if key in registry:
            raise RuntimeError(f"Duplicate desktop command registration: {target}.{name}")
        registry[key] = DesktopCommand(
            target=target,
            name=name,
            access=access,
            history_mode=("reversible" if all_reversible or name in reversible else "non_undoable"),
        )


def _desktop_commands() -> dict[tuple[DesktopTarget, str], DesktopCommand]:
    registry: dict[tuple[DesktopTarget, str], DesktopCommand] = {}
    for group in DESKTOP_COMMAND_GROUPS:
        _register_commands(
            registry,
            target=group.target,
            access=group.access,
            names=group.names,
            reversible=group.reversible,
            all_reversible=group.all_reversible,
        )
    return registry


DESKTOP_COMMANDS = MappingProxyType(_desktop_commands())


def desktop_command(target: DesktopTarget, name: str) -> DesktopCommand:
    try:
        return DESKTOP_COMMANDS[(target, name)]
    except KeyError as error:
        raise ValueError(f"Unknown desktop command: {target}.{name}") from error


def parse_desktop_target(value: object) -> DesktopTarget:
    if value == "project" or value == "timeline":
        return value
    raise ValueError(f"Unknown desktop command target: {value!r}")
