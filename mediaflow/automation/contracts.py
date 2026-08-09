from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mediaflow.domain.audio import AUDIO_EFFECT_DEFINITIONS
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.runtime_capabilities import (
    CAPABILITY_CATALOG,
    CapabilityDefinition,
)
from mediaflow.domain.visual_effects import VISUAL_EFFECT_DEFINITIONS

AUTOMATION_PROTOCOL: Literal["mediaflow-editor"] = "mediaflow-editor"
AUTOMATION_VERSION: Literal[4] = 4


class AutomationRequest(DomainModel):
    protocol: Literal["mediaflow-editor"] = AUTOMATION_PROTOCOL
    version: Literal[4] = AUTOMATION_VERSION
    operation: str
    project: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    base_revision: int | None = Field(default=None, ge=0)
    actor: ActorIdentity
    client_id: str = Field(min_length=1)
    undo_group_id: str | None = None


class AutomationError(DomainModel):
    code: str
    type: str
    message: str


class AutomationSuccessResponse(DomainModel):
    protocol: Literal["mediaflow-editor"] = AUTOMATION_PROTOCOL
    version: Literal[4] = AUTOMATION_VERSION
    request_id: str | None = None
    ok: Literal[True] = True
    result: dict[str, Any]


class AutomationFailureResponse(DomainModel):
    protocol: Literal["mediaflow-editor"] = AUTOMATION_PROTOCOL
    version: Literal[4] = AUTOMATION_VERSION
    request_id: str | None = None
    ok: Literal[False] = False
    error: AutomationError


class AutomationTransport(DomainModel):
    lifecycle: Literal["resident-editor-service"] = "resident-editor-service"
    command: str = "JSON-RPC 2.0 over authenticated loopback HTTP"
    events: str = "authenticated loopback WebSocket with durable cursor replay"
    cli: str = "thin start-on-demand Editor Service client"


class AutomationOperationContract(DomainModel):
    name: str
    project_access: Literal["none", "create", "read", "write"]
    execution_mode: Literal["atomic", "task"]
    history_mode: Literal["reversible", "non_undoable"]
    idempotency: Literal["none", "optional"]
    required_capabilities: list[str]
    arguments_schema: dict[str, Any]
    result_schema: dict[str, Any]


class AutomationContract(DomainModel):
    product: str = PRODUCT_NAME
    protocol: Literal["mediaflow-editor"] = AUTOMATION_PROTOCOL
    version: Literal[4] = AUTOMATION_VERSION
    default_project_root: str
    transport: AutomationTransport = Field(default_factory=AutomationTransport)
    request_schema: dict[str, Any]
    success_response_schema: dict[str, Any]
    error_response_schema: dict[str, Any]
    capabilities: list[CapabilityDefinition]
    editor_field_catalogs: dict[str, dict[str, Any]]
    operations: list[AutomationOperationContract]


def inline_model_schema(model: type[DomainModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value: Any) -> Any:
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.rsplit("/", 1)[-1]
            merged = {
                **definitions[name],
                **{
                    key: item
                    for key, item in value.items()
                    if key != "$ref"
                },
            }
            return resolve(merged)
        resolved = {key: resolve(item) for key, item in value.items()}
        discriminator = resolved.get("discriminator")
        if isinstance(discriminator, dict):
            resolved["discriminator"] = {
                key: item
                for key, item in discriminator.items()
                if key != "mapping"
            }
        return resolved

    return resolve(schema)


def describe_contract() -> dict[str, Any]:
    from mediaflow.automation.operation_registry import OPERATIONS
    from mediaflow.infrastructure.storage_paths import default_project_root

    contract = AutomationContract(
        default_project_root=default_project_root(),
        request_schema=inline_model_schema(AutomationRequest),
        success_response_schema=inline_model_schema(AutomationSuccessResponse),
        error_response_schema=inline_model_schema(AutomationFailureResponse),
        capabilities=list(CAPABILITY_CATALOG),
        editor_field_catalogs={
            "visual_effects": {
                kind.value: definition.model_dump(mode="json")
                for kind, definition in VISUAL_EFFECT_DEFINITIONS.items()
            },
            "audio_effects": {
                kind.value: definition.model_dump(mode="json")
                for kind, definition in AUDIO_EFFECT_DEFINITIONS.items()
            },
        },
        operations=[
            AutomationOperationContract(
                name=name,
                project_access=definition.project_access,
                execution_mode=definition.execution_mode,
                history_mode=definition.history_mode,
                idempotency=definition.idempotency,
                required_capabilities=list(definition.required_capabilities),
                arguments_schema=inline_model_schema(
                    definition.arguments_model
                ),
                result_schema=inline_model_schema(definition.result_model),
            )
            for name, definition in OPERATIONS.items()
        ],
    )
    return contract.model_dump(mode="json")
