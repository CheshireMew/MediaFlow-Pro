from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from .model_base import DomainModel

EditorFieldKind = Literal["number", "integer", "boolean", "string", "color", "choice"]
EditorFieldControl = Literal["slider", "number", "toggle", "text", "color", "select"]
EditorFieldTimeline = Literal["none", "keyframe", "interval"]
EditorFieldScalar = str | float | int | bool
EditorFieldTarget = Literal["layer", "parameter", "theme", "data"]


class EditorFieldChoice(DomainModel):
    value: EditorFieldScalar
    label: str

    @field_validator("label")
    @classmethod
    def non_empty_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editor field choice labels cannot be empty")
        return value


class EditorFieldConstraints(DomainModel):
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = Field(default=None, gt=0)
    choices: list[EditorFieldChoice] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_range(self) -> EditorFieldConstraints:
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Editor field minimum cannot exceed maximum")
        return self


def editor_field_value_matches(kind: EditorFieldKind, value: JsonValue) -> bool:
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    return isinstance(value, str)


class EditorFieldDescriptor(DomainModel):
    id: str
    label: str
    description: str
    group: str
    kind: EditorFieldKind
    control: EditorFieldControl
    default: EditorFieldScalar
    unit: str | None
    constraints: EditorFieldConstraints
    options_source: str | None
    timeline: EditorFieldTimeline

    @field_validator("id", "label", "group")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editor field identifiers, labels, and groups cannot be empty")
        return value

    @model_validator(mode="after")
    def coherent_definition(self) -> EditorFieldDescriptor:
        controls: dict[EditorFieldKind, set[EditorFieldControl]] = {
            "number": {"slider", "number"},
            "integer": {"slider", "number"},
            "boolean": {"toggle"},
            "string": {"text"},
            "color": {"color"},
            "choice": {"select"},
        }
        if self.control not in controls[self.kind]:
            raise ValueError(
                f"Editor field {self.id} control does not match kind {self.kind}"
            )
        self.validate_value(self.default)
        if self.kind == "choice":
            if not self.constraints.choices and self.options_source is None:
                raise ValueError(
                    f"Choice field {self.id} needs choices or an options source"
                )
        elif self.constraints.choices:
            raise ValueError("Only choice fields can declare choices")
        return self

    def validate_value(self, value: JsonValue) -> None:
        if not editor_field_value_matches(self.kind, value):
            raise ValueError(f"Editor field {self.id} value does not match {self.kind}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.constraints.minimum is not None and value < self.constraints.minimum:
                raise ValueError(f"Editor field {self.id} is below minimum")
            if self.constraints.maximum is not None and value > self.constraints.maximum:
                raise ValueError(f"Editor field {self.id} exceeds maximum")
        choices = [item.value for item in self.constraints.choices]
        if choices and value not in choices:
            raise ValueError(f"Editor field {self.id} is not an allowed choice")


class EditorFieldValue(DomainModel):
    path: str
    target: EditorFieldTarget
    source_id: str
    descriptor: EditorFieldDescriptor
    value: JsonValue
    locked: bool = False

    @model_validator(mode="after")
    def valid_value(self) -> EditorFieldValue:
        self.descriptor.validate_value(self.value)
        return self
