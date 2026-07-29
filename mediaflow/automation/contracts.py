from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mediaflow.domain.model_base import DomainModel

AUTOMATION_PROTOCOL: Literal["mediaflow-cli"] = "mediaflow-cli"
AUTOMATION_VERSION: Literal[1] = 1


class AutomationRequest(DomainModel):
    protocol: Literal["mediaflow-cli"] = AUTOMATION_PROTOCOL
    version: Literal[1] = AUTOMATION_VERSION
    operation: str
    project: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


def describe_contract() -> dict[str, Any]:
    from mediaflow.automation.operation_registry import OPERATIONS

    return {
        "protocol": AUTOMATION_PROTOCOL,
        "version": AUTOMATION_VERSION,
        "transport": {
            "lifecycle": "short-process",
            "input": "single JSON object from a file or stdin",
            "output": "single JSON object on stdout",
        },
        "features": {
            "editable_web_media": True,
            "cooperative_desktop_updates": True,
            "remote_web_pages": False,
            "persistent_service": False,
            "web_keyframes": True,
            "web_brand_themes": True,
            "web_responsive_variants": True,
            "web_data_snapshots": True,
            "web_batch_variants": True,
            "web_field_locks_and_diff": True,
            "web_template_rebinding": True,
            "web_multi_format_export": True,
            "ai_transcript_edit_plans": True,
        },
        "operations": [
            {
                "name": name,
                "mutates_project": definition.mutates_project,
                "arguments_schema": definition.arguments_schema,
            }
            for name, definition in OPERATIONS.items()
        ],
    }


def validate_arguments(operation: str, arguments: dict[str, Any]) -> None:
    from mediaflow.automation.operation_registry import OPERATIONS

    schema = OPERATIONS[operation].arguments_schema
    properties = schema["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError(
            f"Unknown arguments for {operation}: {sorted(unknown)}"
        )
    missing = [
        name for name in schema["required"] if name not in arguments
    ]
    if missing:
        raise ValueError(f"Missing arguments for {operation}: {missing}")
    expected_types: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for name, value in arguments.items():
        rule = properties[name]
        expected = rule.get("type")
        if expected and (
            not isinstance(value, expected_types[expected])
            or (
                expected in {"integer", "number"}
                and isinstance(value, bool)
            )
        ):
            raise ValueError(f"arguments.{name} must be {expected}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(
                f"arguments.{name} must be one of {rule['enum']}"
            )
        item_rule = rule.get("items")
        if (
            item_rule
            and isinstance(value, list)
            and any(
                not isinstance(
                    item,
                    expected_types[item_rule["type"]],
                )
                for item in value
            )
        ):
            raise ValueError(
                f"arguments.{name} contains an invalid item"
            )
