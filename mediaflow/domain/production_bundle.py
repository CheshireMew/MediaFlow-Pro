from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .collaboration import ActorIdentity
from .enums import ExportFormat
from .exports import ExportPreset
from .model_base import DomainModel
from .portable_timeline import PortableTimelineProfile
from .project import ProjectProfile
from .settings import AsrSettings
from .speech_review import (
    SpeechReviewResult,
    SpeechReviewRules,
    SpeechReviewSegment,
    review_speech_segments,
)
from .task_commands import SequenceBuildUnit


class ProductionProjectSpec(DomainModel):
    name: str | None = Field(default=None, min_length=1)
    directory_name: str | None = Field(default=None, min_length=1)
    existing_path: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def create_or_reuse(self) -> ProductionProjectSpec:
        if self.existing_path is None:
            if self.name is None or self.directory_name is None:
                raise ValueError(
                    "project.name and project.directory_name are required when creating a project"
                )
        else:
            if self.name is not None or self.directory_name is not None:
                raise ValueError(
                    "project.existing_path cannot be combined with project.name or "
                    "project.directory_name"
                )
            if not Path(self.existing_path).expanduser().is_absolute():
                raise ValueError("project.existing_path must be absolute")
        return self


class ProductionCoverExpectation(DomainModel):
    source_id: str = Field(min_length=1)
    max_duration_frames: int = Field(default=1, ge=1, le=12)


class ProductionTimelineSpec(DomainModel):
    path: str = Field(min_length=1)
    subtitle_track_ids: list[str] = Field(default_factory=list)
    required_audio_source_ids: list[str] = Field(default_factory=list)
    cover: ProductionCoverExpectation | None = None

    @model_validator(mode="after")
    def unique_expectations(self) -> ProductionTimelineSpec:
        if len(self.subtitle_track_ids) != len(set(self.subtitle_track_ids)):
            raise ValueError("timeline.subtitle_track_ids must be unique")
        if len(self.required_audio_source_ids) != len(set(self.required_audio_source_ids)):
            raise ValueError("timeline.required_audio_source_ids must be unique")
        return self


class SpeechSimilarityPolicy(SpeechReviewRules):
    segments: list[SpeechReviewSegment] = Field(default_factory=list)
    auto_transcribe: bool = False
    dialogue_track_id: str | None = Field(default=None, min_length=1)
    asr: AsrSettings | None = None
    transcription_timeout: float = Field(default=3600, gt=0, le=7000)

    @model_validator(mode="after")
    def unique_segments(self) -> SpeechSimilarityPolicy:
        identifiers = [segment.id for segment in self.segments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("speech_review.segments ids must be unique")
        if not self.segments and not self.auto_transcribe:
            raise ValueError("speech_review requires segments or auto_transcribe=true")
        if self.auto_transcribe and self.dialogue_track_id is None:
            raise ValueError("speech_review.dialogue_track_id is required for auto transcription")
        return self


class ProductionOutputSpec(DomainModel):
    output_path: str = Field(min_length=1)
    format: ExportFormat | None = None
    preset: ExportPreset | None = None
    build_units: list[SequenceBuildUnit] = Field(default_factory=list)
    overwrite: bool = False
    timeout: float = Field(default=3600, gt=0, le=7000)

    @model_validator(mode="after")
    def absolute_output(self) -> ProductionOutputSpec:
        if not Path(self.output_path).expanduser().is_absolute():
            raise ValueError("output.output_path must be absolute")
        unit_ids = [unit.id for unit in self.build_units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("output.build_units ids must be unique")
        if self.build_units:
            if self.build_units[0].start_frame != 0:
                raise ValueError("output.build_units must start at frame 0")
            previous_end: int | None = None
            for unit in self.build_units:
                if previous_end is not None and unit.start_frame != previous_end:
                    raise ValueError("output.build_units must be ordered and contiguous")
                previous_end = unit.end_frame
        return self


class ProductionBundle(DomainModel):
    protocol: Literal["mediaflow-production-bundle"] = "mediaflow-production-bundle"
    version: Literal[1] = 1
    request_id: str = Field(min_length=1)
    actor: ActorIdentity
    client_id: str = Field(min_length=1)
    project: ProductionProjectSpec
    timeline: ProductionTimelineSpec
    output: ProductionOutputSpec
    speech_review: SpeechSimilarityPolicy | None = None
    version_name: str = Field(default="Agent production delivery", min_length=1)


SpeechSimilarityReview = SpeechReviewResult


class ProductionCoverInspection(DomainModel):
    requested: bool
    source_id: str | None = None
    clip_id: str | None = None
    duration_seconds: float | None = None
    duration_frames: float | None = None
    starts_at_frame_zero: bool


class ProductionSubtitleInspection(DomainModel):
    track_id: str
    caption_count: int = Field(gt=0)


class ProductionAudioSourceInspection(DomainModel):
    source_id: str
    active_clip_count: int = Field(gt=0)
    active_duration_seconds: float = Field(gt=0)
    track_ids: list[str] = Field(min_length=1)


class ProductionBundleInspection(DomainModel):
    status: Literal["ready", "review_required"]
    timeline_path: str
    timeline_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    timeline_project_id: str
    portable_profile: PortableTimelineProfile
    project_profile: ProjectProfile
    duration_seconds: float = Field(gt=0)
    source_count: int = Field(ge=0)
    track_count: int = Field(gt=0)
    clip_count: int = Field(ge=0)
    export_strategy: Literal["single-pass", "segmented"]
    build_unit_count: int = Field(ge=0)
    cover: ProductionCoverInspection
    subtitles: list[ProductionSubtitleInspection]
    required_audio_sources: list[ProductionAudioSourceInspection]
    speech_review: SpeechSimilarityReview


def review_similar_speech(policy: SpeechSimilarityPolicy | None) -> SpeechSimilarityReview:
    if policy is None:
        return review_speech_segments(None, SpeechReviewRules())
    return review_speech_segments(policy.segments, policy)
