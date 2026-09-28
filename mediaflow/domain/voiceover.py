from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .model_base import DomainModel, new_id, now_ms

VoiceoverCueStatus = Literal["planned", "recording", "review", "approved"]
VoiceoverLatencyMethod = Literal["acoustic_roundtrip", "manual"]


class VoiceoverLatencyCalibration(DomainModel):
    device_id: str = Field(min_length=1, max_length=512)
    device_name: str = Field(min_length=1, max_length=512)
    latency_samples: int = Field(ge=0)
    sample_rate: int = Field(default=48_000, gt=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    method: VoiceoverLatencyMethod = "manual"
    measured_at: int = Field(default_factory=now_ms)


class VoiceoverTake(DomainModel):
    id: str = Field(default_factory=new_id)
    cue_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    duration_frames: int = Field(gt=0)
    notes: str = Field(default="", max_length=2000)
    rating: int = Field(default=0, ge=0, le=5)
    latency_compensation_samples: int = Field(default=0, ge=0)
    latency_sample_rate: int = Field(default=48_000, gt=0)
    calibration_device_id: str | None = Field(default=None, min_length=1, max_length=512)
    calibration_measured_at: int | None = Field(default=None, ge=0)
    archived: bool = False
    created_at: int = Field(default_factory=now_ms)


class VoiceoverCue(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str = Field(min_length=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=10_000)
    speaker: str = Field(default="旁白", min_length=1, max_length=120)
    notes: str = Field(default="", max_length=4000)
    status: VoiceoverCueStatus = "planned"
    selected_take_id: str | None = Field(default=None, min_length=1)
    placed_clip_id: str | None = Field(default=None, min_length=1)
    archived: bool = False
    revision: int = Field(default=0, ge=0)
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)
    takes: list[VoiceoverTake] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_cue(self) -> VoiceoverCue:
        if self.end_frame <= self.start_frame:
            raise ValueError("旁白提示的结束帧必须晚于开始帧")
        take_ids = [item.id for item in self.takes]
        if len(take_ids) != len(set(take_ids)):
            raise ValueError("旁白 take 标识不能重复")
        if any(item.cue_id != self.id for item in self.takes):
            raise ValueError("旁白 take 属于其它提示")
        if self.selected_take_id is not None:
            selected = next(
                (item for item in self.takes if item.id == self.selected_take_id),
                None,
            )
            if selected is None or selected.archived:
                raise ValueError("选中的旁白 take 不存在或已归档")
        return self
