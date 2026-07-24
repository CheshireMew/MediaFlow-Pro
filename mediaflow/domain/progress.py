from __future__ import annotations

from typing import Literal

from pydantic import computed_field, model_validator

from mediaflow.domain.model_base import DomainModel

ProgressMode = Literal["indeterminate", "determinate"]
ProgressUnit = Literal[
    "bytes",
    "frames",
    "items",
    "media_seconds",
    "percent",
    "samples",
    "task",
]


class OperationProgress(DomainModel):
    """Observable progress for one currently running operation.

    Determinate progress always carries measured work units. Indeterminate
    progress deliberately carries no fabricated percentage.
    """

    mode: ProgressMode
    message_code: str
    completed: float | None = None
    total: float | None = None
    unit: ProgressUnit | None = None
    item_index: int | None = None
    item_total: int | None = None
    item_label: str | None = None
    overall_completed: float | None = None
    overall_total: float | None = None
    overall_unit: ProgressUnit | None = None

    @model_validator(mode="after")
    def validate_measurement(self) -> OperationProgress:
        if not self.message_code.strip():
            raise ValueError("Progress message code cannot be empty")
        if self.mode == "indeterminate":
            if self.completed is not None or self.total is not None or self.unit is not None:
                raise ValueError("Indeterminate progress cannot carry measured work")
            self._validate_context()
            return self
        if self.completed is None or self.total is None or self.unit is None:
            raise ValueError("Determinate progress requires completed, total, and unit")
        if self.total <= 0:
            raise ValueError("Determinate progress total must be positive")
        if self.completed < 0 or self.completed > self.total:
            raise ValueError("Determinate progress completed work must be within its total")
        self._validate_context()
        return self

    def _validate_context(self) -> None:
        item_values = (self.item_index, self.item_total)
        if any(value is not None for value in item_values):
            if self.item_index is None or self.item_total is None:
                raise ValueError("Progress item context requires index and total")
            if self.item_total <= 0 or not 1 <= self.item_index <= self.item_total:
                raise ValueError("Progress item context is invalid")
        overall_values = (
            self.overall_completed,
            self.overall_total,
            self.overall_unit,
        )
        if any(value is not None for value in overall_values):
            if (
                self.overall_completed is None
                or self.overall_total is None
                or self.overall_unit is None
            ):
                raise ValueError("Overall progress requires completed, total, and unit")
            if self.overall_total <= 0:
                raise ValueError("Overall progress total must be positive")
            if not 0 <= self.overall_completed <= self.overall_total:
                raise ValueError("Overall completed work must be within its total")

    @computed_field
    @property
    def percent(self) -> float | None:
        if self.mode != "determinate" or self.completed is None or self.total is None:
            return None
        return self.completed / self.total * 100.0

    @computed_field
    @property
    def overall_percent(self) -> float | None:
        if self.overall_completed is None or self.overall_total is None:
            return None
        return self.overall_completed / self.overall_total * 100.0

    @classmethod
    def indeterminate(cls, message_code: str) -> OperationProgress:
        return cls(mode="indeterminate", message_code=message_code)

    @classmethod
    def determinate(
        cls,
        message_code: str,
        *,
        completed: float,
        total: float,
        unit: ProgressUnit,
    ) -> OperationProgress:
        return cls(
            mode="determinate",
            message_code=message_code,
            completed=float(completed),
            total=float(total),
            unit=unit,
        )

    def with_task_context(
        self,
        *,
        item_index: int,
        item_total: int,
        item_label: str,
        overall_completed: float,
        overall_total: float,
        overall_unit: ProgressUnit,
    ) -> OperationProgress:
        payload = self.model_dump(mode="python", exclude_computed_fields=True)
        payload.update(
            {
                "item_index": item_index,
                "item_total": item_total,
                "item_label": item_label,
                "overall_completed": overall_completed,
                "overall_total": overall_total,
                "overall_unit": overall_unit,
            }
        )
        return OperationProgress.model_validate(payload)
