from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from mediaflow.domain.web_manifest import WebDataField
from mediaflow.domain.web_manifest_primitives import WebFieldConstraint
from mediaflow.domain.web_state import WebLayerOverride


class WebFieldValidator:
    """The shared value and constraint boundary for editable-media fields."""

    @classmethod
    def layer_value(
        cls,
        layer_id: str,
        field: str,
        value: object,
        constraint: WebFieldConstraint | None,
    ) -> JsonValue:
        candidate = WebLayerOverride.model_validate({field: value})
        validated = getattr(candidate, field)
        cls.constraint(layer_id, field, validated, constraint)
        return cast(JsonValue, validated)

    @staticmethod
    def data_value(field: WebDataField, value: object) -> None:
        field_id = field.id
        kind = field.kind
        valid = {
            "string": isinstance(value, str),
            "date": isinstance(value, str),
            "media-source": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "list": isinstance(value, list),
            "table": isinstance(value, list) and all(isinstance(row, dict) for row in value),
            "json": isinstance(value, (dict, list, str, int, float, bool)) or value is None,
        }.get(kind, False)
        if not valid:
            raise ValueError(f"Data field {field_id} does not match kind {kind}")
        if kind != "table" or not field.columns:
            return
        if not isinstance(value, list):
            raise ValueError(f"Data field {field_id} table value must be a list")
        columns = {item.id: item.kind for item in field.columns}
        for index, row in enumerate(value):
            if not isinstance(row, dict):
                raise ValueError(f"Data field {field_id} row {index} must be an object")
            missing = set(columns) - set(row)
            unknown = set(row) - set(columns)
            if missing or unknown:
                raise ValueError(
                    f"Data field {field_id} row {index} columns do not match; "
                    f"missing={sorted(missing)}, unknown={sorted(unknown)}"
                )
            for column_id, column_kind in columns.items():
                cell = row[column_id]
                cell_valid = {
                    "string": isinstance(cell, str),
                    "date": isinstance(cell, str),
                    "media-source": isinstance(cell, str),
                    "number": isinstance(cell, (int, float)) and not isinstance(cell, bool),
                    "boolean": isinstance(cell, bool),
                }[column_kind]
                if not cell_valid:
                    raise ValueError(
                        f"Data field {field_id} row {index} column {column_id} "
                        f"does not match kind {column_kind}"
                    )

    @staticmethod
    def constraint(
        owner_id: str,
        field: str,
        value: object,
        constraint: WebFieldConstraint | None,
    ) -> None:
        if value is None or constraint is None:
            return
        if constraint.choices and str(value) not in constraint.choices:
            raise ValueError(f"Layer {owner_id} field {field} is outside its choices")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if constraint.minimum is not None and value < constraint.minimum:
                raise ValueError(f"Layer {owner_id} field {field} is below its minimum")
            if constraint.maximum is not None and value > constraint.maximum:
                raise ValueError(f"Layer {owner_id} field {field} exceeds its maximum")
            if constraint.step is not None:
                origin = constraint.minimum or 0.0
                steps = (float(value) - origin) / constraint.step
                if abs(steps - round(steps)) > 1e-7:
                    raise ValueError(f"Layer {owner_id} field {field} does not match its step")
