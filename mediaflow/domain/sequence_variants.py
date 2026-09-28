from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .audio import AudioBus, AudioEffect
from .model_base import DomainModel, now_ms
from .project import Sequence
from .subtitles import SubtitlePlacement
from .timeline import TimelineState

VariantReframeMode = Literal["fit", "center_fill"]


class SequenceVariantSpec(DomainModel):
    preset_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=240)
    width: int = Field(ge=16, le=8192)
    height: int = Field(ge=16, le=8192)
    reframe_mode: VariantReframeMode = "center_fill"

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Variant name cannot be blank")
        return normalized

    @model_validator(mode="after")
    def encoder_safe_dimensions(self) -> SequenceVariantSpec:
        if self.width % 2 or self.height % 2:
            raise ValueError("Variant dimensions must be even for delivery encoders")
        return self


class SequenceVariantRecord(DomainModel):
    sequence_id: str = Field(min_length=1)
    source_sequence_id: str = Field(min_length=1)
    preset_id: str = Field(min_length=1)
    reframe_mode: VariantReframeMode
    source_timeline_revision: int = Field(ge=0)
    baseline_timeline: TimelineState | None = None
    baseline_audio_buses: list[AudioBus] = Field(default_factory=list)
    baseline_audio_effects: list[AudioEffect] = Field(default_factory=list)
    baseline_subtitle_placements: list[SubtitlePlacement] = Field(default_factory=list)
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)


class SequenceVariantConflict(DomainModel):
    path: str = Field(min_length=1)
    baseline: Any = None
    local: Any = None
    source: Any = None


class SequenceVariantChange(DomainModel):
    path: str = Field(min_length=1)
    action: Literal["add", "remove", "update", "reorder"]


class SequenceVariantPlanItem(DomainModel):
    preset_id: str = Field(min_length=1)
    sequence_id: str | None = None
    action: Literal["create", "reuse", "refresh", "conflict"]
    source_timeline_revision: int = Field(ge=0)
    changes: list[SequenceVariantChange] = Field(default_factory=list)
    conflicts: list[SequenceVariantConflict] = Field(default_factory=list)


class SequenceVariantPlan(DomainModel):
    source_sequence_id: str = Field(min_length=1)
    items: list[SequenceVariantPlanItem]


class SequenceVariantSnapshot(DomainModel):
    timeline: TimelineState
    audio_buses: list[AudioBus]
    audio_effects: list[AudioEffect]
    subtitle_placements: list[SubtitlePlacement]
    record: SequenceVariantRecord


class SequenceVariantGeneration(DomainModel):
    records: list[SequenceVariantRecord]
    sequences: list[Sequence]
    created_sequence_ids: list[str]
    archived_sequence_ids: list[str]
    reused_sequence_ids: list[str]
    refreshed_sequence_ids: list[str] = Field(default_factory=list)
    plans: list[SequenceVariantPlanItem] = Field(default_factory=list)
