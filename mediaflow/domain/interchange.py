from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .enums import ClipMediaKind
from .model_base import DomainModel
from .portable_timeline import LoadedPortableTimeline
from .project import ProjectProfile
from .timeline import ClipAudio


class InterchangeRawKeyframe(DomainModel):
    frame: int = Field(ge=0)
    value: str = Field(min_length=1)


class InterchangeClipAdjustment(DomainModel):
    position: str | None = None
    scale: str | None = None
    rotation: str | None = None
    opacity: str | None = None
    crop_left: str | None = None
    crop_top: str | None = None
    crop_right: str | None = None
    crop_bottom: str | None = None
    animations: dict[str, list[InterchangeRawKeyframe]] = Field(default_factory=dict)


class InterchangeNativeClipSpec(DomainModel):
    clip_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    timeline_start: int = Field(ge=0)
    source_in: int = Field(ge=0)
    duration: int = Field(gt=0)
    media_kind: ClipMediaKind
    speed_numerator: int = 1
    speed_denominator: int = Field(default=1, gt=0)
    pitch_compensation: bool = True
    audio: ClipAudio = Field(default_factory=ClipAudio)
    adjustment: InterchangeClipAdjustment = Field(default_factory=InterchangeClipAdjustment)

    @model_validator(mode="after")
    def supported_speed(self) -> InterchangeNativeClipSpec:
        if self.speed_numerator == 0:
            raise ValueError("Interchange clip speed cannot be zero")
        speed = abs(self.speed_numerator / self.speed_denominator)
        if not 0.25 <= speed <= 4.0:
            raise ValueError("Interchange clip speed must be between 0.25x and 4x")
        return self


class InterchangeTransitionSpec(DomainModel):
    left_clip_id: str = Field(min_length=1)
    right_clip_id: str = Field(min_length=1)
    duration: int = Field(gt=0)


class InterchangeTimelineSummary(DomainModel):
    timeline_path: str = Field(min_length=1)
    timeline_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    format: Literal["fcpxml", "cmx3600"]
    name: str = Field(min_length=1)
    profile: ProjectProfile
    duration_frames: int = Field(gt=0)
    source_count: int = Field(ge=0)
    track_count: int = Field(gt=0)
    clip_count: int = Field(ge=0)
    caption_count: int = Field(ge=0)
    marker_count: int = Field(ge=0)
    transition_count: int = Field(ge=0)
    missing_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LoadedInterchangeTimeline:
    summary: InterchangeTimelineSummary
    portable: LoadedPortableTimeline
    native_clips: tuple[InterchangeNativeClipSpec, ...]
    transitions: tuple[InterchangeTransitionSpec, ...]
    source_labels: dict[str, str]
    path: Path
