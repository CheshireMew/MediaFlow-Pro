from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .transport_schema import transport_schema_id, transport_schema_type

TYPE_KEY = "$mediaflow_type"
VALUE_KEY = "value"
SCHEMA_KEY = "schema"


def encode_transport(value: Any) -> Any:
    """Encode internal editor values without weakening the public JSON contract."""

    if isinstance(value, Enum):
        return {
            TYPE_KEY: "enum",
            SCHEMA_KEY: transport_schema_id(type(value)),
            VALUE_KEY: encode_transport(value.value),
        }
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return {TYPE_KEY: "path", VALUE_KEY: str(value)}
    if isinstance(value, BaseModel):
        return {
            TYPE_KEY: "model",
            SCHEMA_KEY: transport_schema_id(type(value)),
            VALUE_KEY: value.model_dump(
                mode="json",
                exclude_computed_fields=True,
            ),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            TYPE_KEY: "dataclass",
            SCHEMA_KEY: transport_schema_id(type(value)),
            VALUE_KEY: {
                item.name: encode_transport(getattr(value, item.name))
                for item in fields(value)
            },
        }
    if isinstance(value, tuple):
        return {TYPE_KEY: "tuple", VALUE_KEY: [encode_transport(item) for item in value]}
    if isinstance(value, frozenset):
        return {
            TYPE_KEY: "frozenset",
            VALUE_KEY: [encode_transport(item) for item in value],
        }
    if isinstance(value, set):
        return {TYPE_KEY: "set", VALUE_KEY: [encode_transport(item) for item in value]}
    if isinstance(value, list):
        if value and isinstance(value[0], BaseModel):
            model_type = type(value[0])
            if all(type(item) is model_type for item in value):
                return {
                    TYPE_KEY: "model_list",
                    SCHEMA_KEY: transport_schema_id(model_type),
                    VALUE_KEY: [
                        item.model_dump(
                            mode="json",
                            exclude_computed_fields=True,
                        )
                        for item in value
                    ],
                }
        return [encode_transport(item) for item in value]
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {str(key): encode_transport(item) for key, item in value.items()}
        return {
            TYPE_KEY: "mapping",
            VALUE_KEY: [
                [encode_transport(key), encode_transport(item)]
                for key, item in value.items()
            ],
        }
    raise TypeError(f"Value cannot cross the Editor Service boundary: {type(value)!r}")


def decode_transport(value: Any) -> Any:
    if isinstance(value, list):
        return [decode_transport(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get(TYPE_KEY)
    if not isinstance(kind, str):
        return {str(key): decode_transport(item) for key, item in value.items()}
    payload = value.get(VALUE_KEY)
    if kind == "path":
        return Path(str(payload))
    if kind == "tuple":
        return tuple(decode_transport(item) for item in _require_list(payload, kind))
    if kind == "set":
        return {decode_transport(item) for item in _require_list(payload, kind)}
    if kind == "frozenset":
        return frozenset(decode_transport(item) for item in _require_list(payload, kind))
    if kind == "mapping":
        return {
            decode_transport(pair[0]): decode_transport(pair[1])
            for pair in _require_pairs(payload)
        }
    schema_id = str(value.get(SCHEMA_KEY) or "")
    cls = transport_schema_type(schema_id)
    if kind == "enum":
        return cls(decode_transport(payload))
    if kind == "model":
        if not issubclass(cls, BaseModel) or not isinstance(payload, dict):
            raise ValueError(f"Invalid model transport value: {schema_id}")
        return cls.model_validate(payload)
    if kind == "model_list":
        if not issubclass(cls, BaseModel):
            raise ValueError(f"Invalid model-list transport value: {schema_id}")
        return [cls.model_validate(item) for item in _require_list(payload, kind)]
    if kind == "dataclass":
        decoded = decode_transport(payload)
        if not is_dataclass(cls) or not isinstance(decoded, dict):
            raise ValueError(f"Invalid dataclass transport value: {schema_id}")
        return cls(**decoded)
    raise ValueError(f"Unknown Editor Service transport value: {kind}")

def _require_list(value: Any, kind: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{kind} transport value must be an array")
    return value


def _require_pairs(value: Any) -> list[list[Any]]:
    pairs = _require_list(value, "mapping")
    if not all(isinstance(pair, list) and len(pair) == 2 for pair in pairs):
        raise ValueError("mapping transport value must contain key/value pairs")
    return pairs
