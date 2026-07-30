from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

EDITABLE_MEDIA_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "contracts"
    / "editable-media.v4.schema.json"
)


def _value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value: object, expected: str) -> bool:
    actual = _value_type(value)
    if expected == "number":
        return actual in {"number", "integer"}
    return actual == expected


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _resolve_reference(schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise RuntimeError(
            f"editable-media v4 schema only allows local references: {reference}"
        )
    current: object = schema
    for encoded in reference[2:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise RuntimeError(
                f"editable-media v4 schema references a missing location: {reference}"
            )
        current = current[key]
    if not isinstance(current, dict):
        raise RuntimeError(
            f"editable-media v4 schema reference is not an object: {reference}"
        )
    return current


def _validate_node(
    value: object,
    node: dict[str, Any] | bool,
    root_schema: dict[str, Any],
    location: str,
    errors: list[str],
) -> None:
    if node is True:
        return
    if node is False:
        errors.append(f"{location} is not allowed")
        return
    if "$ref" in node:
        _validate_node(
            value,
            _resolve_reference(root_schema, str(node["$ref"])),
            root_schema,
            location,
            errors,
        )
        return

    declared_type = node.get("type")
    expected_types = (
        []
        if declared_type is None
        else declared_type
        if isinstance(declared_type, list)
        else [declared_type]
    )
    if expected_types and not any(
        _type_matches(value, str(expected)) for expected in expected_types
    ):
        errors.append(
            f"{location} must be {' / '.join(map(str, expected_types))}; "
            f"got {_value_type(value)}"
        )
        return
    if "const" in node and _canonical(value) != _canonical(node["const"]):
        errors.append(f"{location} must equal {_canonical(node['const'])}")
    if "enum" in node and all(
        _canonical(value) != _canonical(candidate) for candidate in node["enum"]
    ):
        errors.append(f"{location} is not an allowed editable-media v4 value")

    if isinstance(value, str):
        if "minLength" in node and len(value) < int(node["minLength"]):
            errors.append(f"{location} cannot be empty")
        if "pattern" in node and re.search(str(node["pattern"]), value) is None:
            errors.append(f"{location} does not match the required path or value format")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in node and value < float(node["minimum"]):
            errors.append(f"{location} cannot be less than {node['minimum']}")
        if "maximum" in node and value > float(node["maximum"]):
            errors.append(f"{location} cannot be greater than {node['maximum']}")
        if "exclusiveMinimum" in node and value <= float(node["exclusiveMinimum"]):
            errors.append(f"{location} must be greater than {node['exclusiveMinimum']}")
        if "exclusiveMaximum" in node and value >= float(node["exclusiveMaximum"]):
            errors.append(f"{location} must be less than {node['exclusiveMaximum']}")

    if isinstance(value, list):
        if "minItems" in node and len(value) < int(node["minItems"]):
            errors.append(f"{location} needs at least {node['minItems']} items")
        if node.get("uniqueItems") is True:
            encoded = [_canonical(item) for item in value]
            if len(set(encoded)) != len(encoded):
                errors.append(f"{location} cannot contain duplicate items")
        item_schema = node.get("items")
        if isinstance(item_schema, (dict, bool)):
            for index, item in enumerate(value):
                _validate_node(
                    item,
                    item_schema,
                    root_schema,
                    f"{location}[{index}]",
                    errors,
                )

    if isinstance(value, dict):
        properties = node.get("properties", {})
        for key in node.get("required", []):
            if key not in value:
                errors.append(f"{location}.{key} is required")
        for key, item in value.items():
            if key in properties:
                _validate_node(
                    item,
                    properties[key],
                    root_schema,
                    f"{location}.{key}",
                    errors,
                )
                continue
            additional = node.get("additionalProperties", True)
            if additional is False:
                errors.append(f"{location}.{key} is not an editable-media v4 field")
            elif isinstance(additional, dict):
                _validate_node(
                    item,
                    additional,
                    root_schema,
                    f"{location}.{key}",
                    errors,
                )


@lru_cache(maxsize=1)
def read_editable_media_schema() -> dict[str, Any]:
    return json.loads(EDITABLE_MEDIA_SCHEMA_PATH.read_text(encoding="utf-8"))


def editable_media_schema_errors(document: object) -> list[str]:
    schema = read_editable_media_schema()
    errors: list[str] = []
    _validate_node(document, schema, schema, "$", errors)
    return errors


def validate_editable_media_document(document: object) -> None:
    errors = editable_media_schema_errors(document)
    if errors:
        raise ValueError(
            "editable-media v4 schema validation failed:\n- "
            + "\n- ".join(errors)
        )
