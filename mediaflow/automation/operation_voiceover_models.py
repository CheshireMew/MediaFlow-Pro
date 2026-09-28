from __future__ import annotations

from pydantic import Field

from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.voiceover import (
    VoiceoverCue,
    VoiceoverCueStatus,
    VoiceoverLatencyCalibration,
    VoiceoverTake,
)

from .operation_model_common import EmptyArguments, SequenceArguments


class VoiceoverCueListArguments(SequenceArguments):
    include_archived: bool = False


class VoiceoverCueCreateArguments(SequenceArguments):
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=10_000)
    speaker: str = Field(default="旁白", min_length=1, max_length=120)
    notes: str = Field(default="", max_length=4000)


class VoiceoverCueUpdateArguments(DomainModel):
    cue_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=10_000)
    speaker: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=4000)
    status: VoiceoverCueStatus


class VoiceoverCueArchiveArguments(DomainModel):
    cue_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)


class VoiceoverTakeAddArguments(DomainModel):
    cue_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    notes: str = Field(default="", max_length=2000)
    calibration_device_id: str | None = Field(default=None, min_length=1, max_length=512)


class VoiceoverTakeUpdateArguments(DomainModel):
    take_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    notes: str = Field(default="", max_length=2000)
    rating: int = Field(ge=0, le=5)


class VoiceoverTakeSelectArguments(DomainModel):
    cue_id: str = Field(min_length=1)
    take_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)


class VoiceoverTakeArchiveArguments(DomainModel):
    take_id: str = Field(min_length=1)


class VoiceoverTakePlaceArguments(DomainModel):
    cue_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)


class VoiceoverLatencyCalibrationListArguments(EmptyArguments):
    pass


class VoiceoverLatencyCalibrationGetArguments(DomainModel):
    device_id: str = Field(min_length=1, max_length=512)


class VoiceoverLatencyCalibrationSetArguments(VoiceoverLatencyCalibrationGetArguments):
    device_name: str = Field(min_length=1, max_length=512)
    latency_samples: int = Field(ge=0)
    sample_rate: int = Field(default=48_000, gt=0)


class VoiceoverLatencyCalibrationAnalyzeArguments(VoiceoverLatencyCalibrationGetArguments):
    device_name: str = Field(min_length=1, max_length=512)
    recording: str = Field(min_length=1)
    marker_offset_samples: int = Field(default=12_000, ge=0)


class VoiceoverCueResult(DomainModel):
    cue: VoiceoverCue


class VoiceoverCueListResult(DomainModel):
    cues: list[VoiceoverCue]


class VoiceoverTakeResult(DomainModel):
    cue: VoiceoverCue
    take: VoiceoverTake


class VoiceoverTakeUpdateResult(DomainModel):
    take: VoiceoverTake


class VoiceoverLatencyCalibrationResult(DomainModel):
    calibration: VoiceoverLatencyCalibration | None


class VoiceoverLatencyCalibrationListResult(DomainModel):
    calibrations: list[VoiceoverLatencyCalibration]
