from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mediaflow.domain.model_base import DomainModel


class SpeechTranscribeArguments(DomainModel):
    input_path: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    language: str | None = None
    model: str | None = None
    device: Literal["auto", "cuda", "cpu"] | None = None
    compute_type: str | None = None
    overwrite: bool = False


class SpeechSegmentResult(DomainModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def positive_range(self) -> SpeechSegmentResult:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Speech segment end must be after start")
        return self


class SpeechTranscriptionResult(DomainModel):
    engine: Literal["faster-whisper-xxl"] = "faster-whisper-xxl"
    engine_version: str
    input_path: str
    input_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    output_path: str
    output_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    language: str
    duration_seconds: float = Field(gt=0)
    segments: list[SpeechSegmentResult] = Field(min_length=1)


class SpeechSynthesizeArguments(DomainModel):
    text: str = Field(min_length=1)
    text_language: str = Field(min_length=1)
    reference_audio: str = Field(min_length=1)
    reference_text: str = Field(min_length=1)
    reference_language: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    auxiliary_reference_audio: list[str] = Field(default_factory=list, max_length=5)
    speed_factor: float = Field(default=1.0, ge=0.5, le=2.0)
    seed: int = -1
    timeout_seconds: int = Field(default=900, ge=30, le=3600)
    overwrite: bool = False


class SpeechSynthesisResult(DomainModel):
    engine: Literal["gpt-sovits-v2pro"] = "gpt-sovits-v2pro"
    engine_version: str
    output_path: str
    output_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    duration_seconds: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    reference_audio_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    device: Literal["cuda", "cpu"]
