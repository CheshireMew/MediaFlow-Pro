from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .model_base import DomainModel
from .project_records import ProjectVersionRecord
from .subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord


class TranscriptSegmentSnapshot(DomainModel):
    segment: SubtitleSegment
    words: list[SubtitleWord] = Field(default_factory=list)


class TranscriptSnapshot(DomainModel):
    content_revision: int = Field(ge=0)
    document: SubtitleDocument
    segments: list[TranscriptSegmentSnapshot]
    recognized_word_count: int = Field(ge=0)
    estimated_word_count: int = Field(ge=0)


class TranscriptEditSelection(DomainModel):
    kind: Literal["words", "segments"]
    ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("Transcript edit selection must contain at least one id")
        return normalized

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Transcript edit selection reason cannot be empty")
        return normalized


class TranscriptEditRequest(DomainModel):
    sequence_id: str
    document_id: str
    expected_content_revision: int = Field(ge=0)
    selections: list[TranscriptEditSelection] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_targets(self) -> TranscriptEditRequest:
        seen: set[tuple[str, str]] = set()
        duplicates: set[str] = set()
        for selection in self.selections:
            for item_id in selection.ids:
                key = (selection.kind, item_id)
                if key in seen:
                    duplicates.add(item_id)
                seen.add(key)
        if duplicates:
            raise ValueError(
                f"Transcript edit plan repeats target ids: {sorted(duplicates)}"
            )
        return self


class TranscriptFrameInterval(DomainModel):
    start_frame: int = Field(ge=0)
    end_frame: int

    @model_validator(mode="after")
    def validate_range(self) -> TranscriptFrameInterval:
        if self.end_frame <= self.start_frame:
            raise ValueError("Transcript edit interval must contain at least one frame")
        return self


class TranscriptResolvedSelection(DomainModel):
    kind: Literal["words", "segments"]
    ids: list[str]
    reason: str
    text: str
    intervals: list[TranscriptFrameInterval]
    timing: Literal["recognized_words", "subtitle_segments"]


class TranscriptEditImpact(DomainModel):
    before_duration_frames: int = Field(ge=0)
    after_duration_frames: int = Field(ge=0)
    removed_duration_frames: int = Field(ge=1)
    affected_track_ids: list[str] = Field(default_factory=list)
    changed_clip_ids: list[str] = Field(default_factory=list)
    created_clip_ids: list[str] = Field(default_factory=list)
    locked_track_ids: list[str] = Field(default_factory=list)


class TranscriptEditPlan(DomainModel):
    version: Literal[1] = 1
    sequence_id: str
    document_id: str
    expected_content_revision: int = Field(ge=0)
    selections: list[TranscriptEditSelection] = Field(min_length=1)
    resolved_selections: list[TranscriptResolvedSelection] = Field(min_length=1)
    intervals: list[TranscriptFrameInterval] = Field(min_length=1)
    impact: TranscriptEditImpact
    warnings: list[str] = Field(default_factory=list)
    plan_digest: str


class TranscriptEditResult(DomainModel):
    plan_digest: str
    recovery_version: ProjectVersionRecord
    removed_word_count: int = Field(ge=0)
    removed_segment_count: int = Field(ge=0)
    before_duration_frames: int = Field(ge=0)
    after_duration_frames: int = Field(ge=0)
    content_revision: int = Field(ge=0)
