from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from threading import RLock
from types import GenericAlias, MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, cast, get_args, get_origin, get_type_hints

from pydantic import create_model

from mediaflow.domain.model_base import DomainModel
from mediaflow.service.desktop_command_catalog import DESKTOP_COMMAND_GROUPS
from mediaflow.service.execution import ServiceWorkload

if TYPE_CHECKING:
    from mediaflow.application.project_mutation_planning import ProjectMutationDocuments
    from mediaflow.domain.collaboration import ProjectMutationPlan

CommandAccess = Literal["read", "write", "runtime"]
HistoryMode = Literal["reversible", "non_undoable"]
DesktopTarget = Literal["project", "timeline"]


class DesktopCommandRequest(DomainModel):
    """Base for generated, command-specific named argument contracts."""


class DesktopCommandResult(DomainModel):
    """Base for generated, command-specific result contracts."""

    value: object


@dataclass(frozen=True, slots=True)
class DesktopCommand:
    target: DesktopTarget
    name: str
    access: CommandAccess
    workload: ServiceWorkload
    history_mode: HistoryMode = "non_undoable"

    @property
    def handler(self) -> Any:
        return _command_handler(self.target, self.name)[0]

    @property
    def bind_receiver(self) -> bool:
        return _command_handler(self.target, self.name)[1]

    @property
    def signature(self) -> inspect.Signature:
        return command_contract(self.target, self.name, self.handler).signature

    @property
    def request_model(self) -> type[DesktopCommandRequest]:
        return command_contract(self.target, self.name, self.handler).request_model

    @property
    def result_model(self) -> type[DesktopCommandResult]:
        return command_contract(self.target, self.name, self.handler).result_model

    @property
    def schema_id(self) -> str:
        return f"desktop.{self.target}.{self.name}.v2"

    @property
    def starts_task(self) -> bool:
        return self.access == "runtime" and "idempotency_key" in self.signature.parameters

    def validate_request(self, args: object, kwargs: object) -> DesktopCommandRequest:
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError("Desktop command args and kwargs must be an array and object")
        bound = self.signature.bind(*args, **kwargs)
        return self.validate_arguments(bound.arguments)

    def validate_arguments(self, arguments: object) -> DesktopCommandRequest:
        if not isinstance(arguments, dict):
            raise ValueError("Desktop command arguments must be an object")
        return self.request_model.model_validate(arguments)

    @staticmethod
    def request_arguments(request: DesktopCommandRequest) -> dict[str, Any]:
        return {
            name: getattr(request, name)
            for name in request.__class__.model_fields
            if name in request.model_fields_set
        }

    def validate_result(self, value: object) -> object:
        return self.result_model.model_validate({"value": value}).value

    def invoke(
        self,
        receiver: object,
        request: DesktopCommandRequest,
    ) -> Any:
        arguments = self.request_arguments(request)
        if self.bind_receiver:
            return self.handler(receiver, **arguments)
        return self.handler(**arguments)

    def mutation_plan(
        self,
        *,
        sequence_id: str,
        args: list[Any],
        kwargs: dict[str, Any],
        project: ProjectMutationDocuments | None = None,
    ) -> ProjectMutationPlan:
        from mediaflow.application.desktop_mutation_adapter import plan_desktop_project_mutation

        return plan_desktop_project_mutation(
            self.target,
            self.name,
            sequence_id=sequence_id,
            args=args,
            kwargs=kwargs,
            project=project,
        )


@cache
def _project_command_surfaces() -> tuple[type, ...]:
    from mediaflow.editor_project_delivery_commands import EditorProjectDeliveryCommands
    from mediaflow.editor_project_document_commands import EditorProjectDocumentCommands
    from mediaflow.editor_project_media_commands import EditorProjectMediaCommands
    from mediaflow.editor_project_script_timeline_commands import EditorProjectScriptTimelineCommands
    from mediaflow.editor_project_task_commands import EditorProjectTaskWorkflowCommands
    from mediaflow.editor_project_web_commands import EditorProjectWebCommands

    return (
        EditorProjectDocumentCommands,
        EditorProjectMediaCommands,
        EditorProjectScriptTimelineCommands,
        EditorProjectTaskWorkflowCommands,
        EditorProjectWebCommands,
        EditorProjectDeliveryCommands,
    )


@cache
def _command_handler(target: DesktopTarget, name: str) -> tuple[Any, bool]:
    if target == "timeline":
        from mediaflow.application.timeline_editor import TimelineEditor

        descriptor = inspect.getattr_static(TimelineEditor, name)
    else:
        matches = [
            inspect.getattr_static(surface, name)
            for surface in _project_command_surfaces()
            if name in surface.__dict__
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Desktop command must have one project handler: {name}")
        descriptor = matches[0]
    if isinstance(descriptor, property):
        if descriptor.fget is None:
            raise RuntimeError(f"Desktop command property is unreadable: {target}.{name}")
        return descriptor.fget, True
    if isinstance(descriptor, staticmethod):
        return descriptor.__func__, False
    if not callable(descriptor):
        raise RuntimeError(f"Desktop command handler is not callable: {target}.{name}")
    return descriptor, True


def command_signature(source: Any) -> tuple[inspect.Signature, dict[str, Any], Any]:
    signature = inspect.signature(source)
    hints = get_type_hints(source)
    parameters = list(signature.parameters.values())
    if parameters and parameters[0].name in {"self", "cls"}:
        parameters = parameters[1:]
    if any(
        parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        for parameter in parameters
    ):
        raise RuntimeError(f"Desktop command contract is variadic: {source!r}")
    signature = signature.replace(parameters=parameters)
    annotations = {
        parameter.name: _transport_parameter_annotation(hints.get(parameter.name, parameter.annotation))
        for parameter in parameters
    }
    missing = [
        name for name, annotation in annotations.items() if annotation in {inspect.Parameter.empty, Any}
    ]
    if missing:
        raise RuntimeError(f"Desktop command contract has untyped arguments {missing}: {source!r}")
    return signature, annotations, hints.get("return", signature.return_annotation)


def _transport_parameter_annotation(annotation: Any) -> Any:
    """Materialize one-shot iterables before they cross the JSON boundary."""

    if get_origin(annotation) is Iterable:
        arguments = get_args(annotation)
        item_type = arguments[0] if arguments else object
        return GenericAlias(list, item_type)
    return annotation


def command_models(
    target: str,
    name: str,
    signature: inspect.Signature,
    annotations: dict[str, Any],
    result_annotation: Any,
) -> tuple[type[DesktopCommandRequest], type[DesktopCommandResult]]:
    model_prefix = "".join(part.title() for part in f"{target}_{name}".split("_"))
    request_fields = {
        parameter.name: (
            annotations[parameter.name],
            ... if parameter.default is inspect.Parameter.empty else parameter.default,
        )
        for parameter in signature.parameters.values()
    }
    request_model = cast(
        type[DesktopCommandRequest],
        create_model(
            f"{model_prefix}Request",
            __base__=DesktopCommandRequest,
            **cast(dict[str, Any], request_fields),
        ),
    )
    if result_annotation in {inspect.Signature.empty, Any, object}:
        raise RuntimeError(f"Command contract has an untyped result: {target}.{name}")
    result_model = cast(
        type[DesktopCommandResult],
        create_model(
            f"{model_prefix}Result",
            __base__=DesktopCommandResult,
            value=(result_annotation, ...),
        ),
    )
    return request_model, result_model


@dataclass(frozen=True, slots=True)
class CommandContract:
    signature: inspect.Signature
    request_model: type[DesktopCommandRequest]
    result_model: type[DesktopCommandResult]


_CONTRACT_LOCK = RLock()


@cache
def _compile_command_contract(target: str, name: str, handler: Any) -> CommandContract:
    signature, annotations, result_annotation = command_signature(handler)
    try:
        request_model, result_model = command_models(
            target, name, signature, annotations, result_annotation,
        )
    except Exception as error:
        raise RuntimeError(f"Failed to build {target} command contract: {name}") from error
    return CommandContract(signature, request_model, result_model)


def command_contract(target: str, name: str, handler: Any) -> CommandContract:
    # The catalog is needed to bind remote methods during startup; the typed
    # contracts are needed only when a command is actually used. Keep one pair
    # of model classes even when service workers first use a command together.
    with _CONTRACT_LOCK:
        return _compile_command_contract(target, name, handler)


def _register_commands(
    registry: dict[tuple[DesktopTarget, str], DesktopCommand],
    *,
    target: DesktopTarget,
    access: CommandAccess,
    workload: ServiceWorkload,
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
            workload=workload,
            history_mode=("reversible" if all_reversible or name in reversible else "non_undoable"),
        )


def _desktop_commands() -> dict[tuple[DesktopTarget, str], DesktopCommand]:
    registry: dict[tuple[DesktopTarget, str], DesktopCommand] = {}
    for group in DESKTOP_COMMAND_GROUPS:
        _register_commands(
            registry,
            target=group.target,
            access=group.access,
            workload=group.workload,
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
