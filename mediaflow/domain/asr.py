from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol

from pydantic import (
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import AssetFingerprint
from mediaflow.domain.settings import AsrSettings

AsrProgress = Callable[[OperationProgress], None]
NonEmptyText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
    ),
]


@dataclass(frozen=True, slots=True)
class AsrWord:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class AsrSegment:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None
    words: tuple[AsrWord, ...] = ()


@dataclass(frozen=True, slots=True)
class AsrResult:
    language: str
    duration_seconds: float
    segments: tuple[AsrSegment, ...]


class TranscriptionRegionPlan(DomainModel):
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)

    @model_validator(mode="after")
    def positive_range(self) -> TranscriptionRegionPlan:
        if self.end_frame <= self.start_frame:
            raise ValueError("Transcription region end must be after start")
        return self

    @computed_field
    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame


class TranscriptionSourcePlan(DomainModel):
    asset_id: NonEmptyText
    asset_name: NonEmptyText
    fingerprint: AssetFingerprint
    regions: list[TranscriptionRegionPlan] = Field(min_length=1)


class TranscriptionPlan(DomainModel):
    sequence_id: NonEmptyText
    timeline_signature: NonEmptyText
    dialogue_track_id: NonEmptyText
    timeline_start_frame: int = Field(ge=0)
    timeline_end_frame: int = Field(ge=0)
    fps_numerator: int = Field(gt=0)
    fps_denominator: int = Field(gt=0)
    sources: list[TranscriptionSourcePlan]
    asr: AsrSettings

    @model_validator(mode="after")
    def valid_timeline_range(self) -> TranscriptionPlan:
        if self.timeline_end_frame < self.timeline_start_frame:
            raise ValueError("Transcription timeline range is invalid")
        if (
            self.sources
            and self.timeline_end_frame <= self.timeline_start_frame
        ):
            raise ValueError(
                "Executable transcription range must be positive"
            )
        asset_ids = [source.asset_id for source in self.sources]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError(
                "Transcription sources must have unique assets"
            )
        return self

    @computed_field
    @property
    def source_count(self) -> int:
        return len(self.sources)

    @computed_field
    @property
    def region_count(self) -> int:
        return sum(len(source.regions) for source in self.sources)

    @computed_field
    @property
    def recognition_frames(self) -> int:
        return sum(
            region.duration_frames
            for source in self.sources
            for region in source.regions
        )

    @computed_field
    @property
    def recognition_seconds(self) -> float:
        return self.recognition_frames * self.fps_denominator / self.fps_numerator


class AsrEngine(Protocol):
    def transcribe(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult: ...


class RegionAsrPipeline(Protocol):
    def transcribe_region(
        self,
        media_path: str | Path,
        *,
        start_seconds: float,
        end_seconds: float,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult: ...
