from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .model_base import DomainModel, new_id, now_ms

DubbingSessionStatus = Literal[
    "preparing",
    "review",
    "synthesizing",
    "synthesized",
    "committed",
]
DubbingUtteranceStatus = Literal[
    "pending",
    "generated",
    "needs_review",
    "failed",
]
DubbingReviewStatus = Literal["automatic", "accepted", "needs_review"]


class DiarizationTurn(DomainModel):
    speaker: str
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def positive_range(self) -> DiarizationTurn:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Diarization turns require a positive time range")
        return self


class DiarizationSpeechInterval(DomainModel):
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def positive_range(self) -> DiarizationSpeechInterval:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Speech intervals require a positive time range")
        return self


class DiarizationResult(DomainModel):
    engine: str
    engine_version: str
    model: str
    device: str
    exclusive: bool
    turns: tuple[DiarizationTurn, ...]


def _project_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized == "."
        or PureWindowsPath(value).is_absolute()
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError("Dubbing artifact paths must be project-relative")
    return path.as_posix()


class DubbingSettings(DomainModel):
    minimum_speakers: int | None = Field(default=None, ge=1, le=32)
    maximum_speakers: int | None = Field(default=None, ge=1, le=32)
    merge_gap_frames: int = Field(default=6, ge=0)
    reference_min_seconds: float = Field(default=3.0, ge=3.0, le=9.8)
    reference_max_seconds: float = Field(default=9.8, ge=3.0, le=9.8)
    maximum_speed_factor: float = Field(default=1.35, ge=1.0, le=2.0)
    borrow_gap_frames: int = Field(default=12, ge=0)
    seed: int = -1

    @model_validator(mode="after")
    def coherent_ranges(self) -> DubbingSettings:
        if (
            self.minimum_speakers is not None
            and self.maximum_speakers is not None
            and self.minimum_speakers > self.maximum_speakers
        ):
            raise ValueError("Minimum speakers cannot exceed maximum speakers")
        if self.reference_min_seconds > self.reference_max_seconds:
            raise ValueError("Reference minimum duration cannot exceed its maximum")
        return self


class DubbingSpeakerTurn(DomainModel):
    id: str = Field(default_factory=new_id)
    speaker_id: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def positive_range(self) -> DubbingSpeakerTurn:
        if self.end_frame <= self.start_frame:
            raise ValueError("Diarization turns require a positive frame range")
        return self


class DubbingReference(DomainModel):
    id: str = Field(default_factory=new_id)
    speaker_id: str
    path: str
    sha256: str = Field(min_length=64, max_length=64)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)
    text: str
    language: str
    duration_seconds: float = Field(gt=0.0)
    primary: bool = False

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return _project_relative_path(value)

    @field_validator("text", "language")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Dubbing reference text and language are required")
        return normalized

    @model_validator(mode="after")
    def positive_range(self) -> DubbingReference:
        if self.end_frame <= self.start_frame:
            raise ValueError("Dubbing references require a positive frame range")
        return self


class DubbingSpeaker(DomainModel):
    id: str
    label: str
    display_name: str
    review_status: DubbingReviewStatus = "automatic"
    references: list[DubbingReference] = Field(default_factory=list)

    @field_validator("id", "label", "display_name")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Dubbing speaker fields cannot be empty")
        return normalized

    @model_validator(mode="after")
    def coherent_references(self) -> DubbingSpeaker:
        if any(item.speaker_id != self.id for item in self.references):
            raise ValueError("Dubbing reference belongs to another speaker")
        ids = [item.id for item in self.references]
        if len(ids) != len(set(ids)):
            raise ValueError("Dubbing reference identifiers must be unique")
        if sum(item.primary for item in self.references) > 1:
            raise ValueError("A speaker can have only one primary reference")
        return self

    @property
    def primary_reference(self) -> DubbingReference | None:
        return next((item for item in self.references if item.primary), None)


class DubbingUtterance(DomainModel):
    id: str = Field(default_factory=new_id)
    speaker_id: str
    source_segment_ids: list[str] = Field(min_length=1)
    target_segment_ids: list[str] = Field(min_length=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)
    source_text: str
    target_text: str
    status: DubbingUtteranceStatus = "pending"
    review_status: DubbingReviewStatus = "automatic"
    output_path: str | None = None
    output_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    natural_duration_seconds: float | None = Field(default=None, gt=0.0)
    fitted_duration_seconds: float | None = Field(default=None, gt=0.0)
    speed_factor: float = Field(default=1.0, ge=0.5, le=2.0)
    seed: int = -1
    reference_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    issues: list[str] = Field(default_factory=list)

    @field_validator("source_segment_ids", "target_segment_ids")
    @classmethod
    def unique_segment_ids(cls, values: list[str]) -> list[str]:
        normalized = [item.strip() for item in values]
        if any(not item for item in normalized):
            raise ValueError("Dubbing segment identifiers cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Dubbing segment identifiers must be unique")
        return normalized

    @field_validator("source_text", "target_text")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Dubbing utterance text cannot be empty")
        return normalized

    @field_validator("output_path")
    @classmethod
    def valid_optional_path(cls, value: str | None) -> str | None:
        return None if value is None else _project_relative_path(value)

    @model_validator(mode="after")
    def coherent_generation(self) -> DubbingUtterance:
        if self.end_frame <= self.start_frame:
            raise ValueError("Dubbing utterances require a positive frame range")
        generated_fields = (
            self.output_path,
            self.output_sha256,
            self.natural_duration_seconds,
            self.fitted_duration_seconds,
            self.reference_sha256,
        )
        if self.status in {"generated", "needs_review"} and any(
            value is None for value in generated_fields
        ):
            raise ValueError("Generated dubbing utterances require complete output metadata")
        if self.output_path is None and any(
            value is not None for value in generated_fields[1:]
        ):
            raise ValueError("Dubbing output metadata requires an output path")
        return self


class DubbingSession(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    sequence_id: str
    source_document_id: str
    target_document_id: str | None = None
    source_language: str
    target_language: str
    dialogue_track_id: str
    source_timeline_revision: int = Field(ge=0)
    status: DubbingSessionStatus = "preparing"
    settings: DubbingSettings = Field(default_factory=DubbingSettings)
    speakers: list[DubbingSpeaker] = Field(default_factory=list)
    turns: list[DubbingSpeakerTurn] = Field(default_factory=list)
    utterances: list[DubbingUtterance] = Field(default_factory=list)
    diarization_engine: str = "3D-Speaker CAM++"
    diarization_version: str = ""
    diarization_model: str = "3dspeaker-campplus-zh-en-16k"
    synthesis_engine: str = "gpt-sovits-v2pro"
    synthesis_version: str = ""
    master_path: str | None = None
    master_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    master_duration_seconds: float | None = Field(default=None, gt=0.0)
    master_asset_id: str | None = None
    committed_track_id: str | None = None
    committed_clip_id: str | None = None
    revision: int = Field(default=0, ge=0)
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)

    @field_validator("master_path")
    @classmethod
    def valid_optional_path(cls, value: str | None) -> str | None:
        return None if value is None else _project_relative_path(value)

    @field_validator(
        "project_id",
        "sequence_id",
        "source_document_id",
        "source_language",
        "target_language",
        "dialogue_track_id",
    )
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Dubbing session fields cannot be empty")
        return normalized

    @model_validator(mode="after")
    def coherent_aggregate(self) -> DubbingSession:
        speaker_ids = [item.id for item in self.speakers]
        if len(speaker_ids) != len(set(speaker_ids)):
            raise ValueError("Dubbing speaker identifiers must be unique")
        known_speakers = set(speaker_ids)
        if any(item.speaker_id not in known_speakers for item in self.turns):
            raise ValueError("Diarization turn references an unknown speaker")
        utterance_ids = [item.id for item in self.utterances]
        if len(utterance_ids) != len(set(utterance_ids)):
            raise ValueError("Dubbing utterance identifiers must be unique")
        if any(item.speaker_id not in known_speakers for item in self.utterances):
            raise ValueError("Dubbing utterance references an unknown speaker")
        if any(
            left.start_frame > right.start_frame
            or left.end_frame > right.start_frame
            for left, right in zip(
                self.utterances,
                self.utterances[1:],
                strict=False,
            )
        ):
            raise ValueError(
                "Dubbing utterances must be chronological and cannot overlap"
            )
        source_segment_ids = [
            segment_id
            for utterance in self.utterances
            for segment_id in utterance.source_segment_ids
        ]
        if len(source_segment_ids) != len(set(source_segment_ids)):
            raise ValueError("A source subtitle segment cannot belong to multiple utterances")
        if self.status != "preparing":
            if not self.target_document_id or not self.speakers or not self.utterances:
                raise ValueError(
                    "Prepared dubbing sessions require a translation, speakers, and utterances"
                )
            if any(speaker.primary_reference is None for speaker in self.speakers):
                raise ValueError(
                    "Every prepared dubbing speaker requires a primary reference"
                )
        master_fields = (
            self.master_path,
            self.master_sha256,
            self.master_duration_seconds,
        )
        if self.status in {"synthesized", "committed"} and any(
            value is None for value in master_fields
        ):
            raise ValueError("Synthesized dubbing sessions require a master output")
        if self.master_path is None and any(value is not None for value in master_fields[1:]):
            raise ValueError("Dubbing master metadata requires a master path")
        if self.status == "committed" and any(
            value is None
            for value in (
                self.master_asset_id,
                self.committed_track_id,
                self.committed_clip_id,
            )
        ):
            raise ValueError("Committed dubbing sessions require timeline identities")
        return self
