from __future__ import annotations

import time
import uuid
from fractions import Fraction
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from .enums import (
    AssetKind,
    AssetOrigin,
    AssetStatus,
    AudioEffectKind,
    ColorMode,
    ExportFormat,
    SequenceKind,
    TaskKind,
    TaskStatus,
    TrackKind,
    TransitionKind,
    WorkflowStage,
    WorkflowStatus,
)


def new_id() -> str:
    return str(uuid.uuid4())


def now_ms() -> int:
    return int(time.time() * 1000)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=False)


class ProjectProfile(DomainModel):
    width: int = 1920
    height: int = 1080
    fps_numerator: int = 30
    fps_denominator: int = 1
    color_mode: ColorMode = ColorMode.SDR_BT709
    bit_depth: int = 8
    audio_sample_rate: int = 48_000
    audio_channels: int = 2

    @model_validator(mode="after")
    def validate_profile(self) -> ProjectProfile:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Project dimensions must be positive")
        if self.fps_numerator <= 0 or self.fps_denominator <= 0:
            raise ValueError("Project frame rate must be positive")
        if self.bit_depth not in {8, 10, 12, 16}:
            raise ValueError("Project bit depth must be 8, 10, 12, or 16")
        if self.color_mode == ColorMode.HDR10_BT2020_PQ and self.bit_depth < 10:
            raise ValueError("HDR10 projects require at least 10-bit processing")
        if self.audio_sample_rate != 48_000:
            raise ValueError("MediaFlow Pro projects use a 48 kHz audio clock")
        if self.audio_channels not in {1, 2, 6}:
            raise ValueError("Audio channels must be mono, stereo, or 5.1")
        return self

    @computed_field
    @property
    def fps(self) -> float:
        return float(Fraction(self.fps_numerator, self.fps_denominator))


class ExportPreset(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str
    format: ExportFormat
    container: str
    video_codec: str | None
    audio_codec: str | None
    pixel_format: str | None
    quality_mode: str = "crf"
    quality_value: float = 18.0
    preset: str = "medium"
    gop_frames: int = 60
    audio_bitrate: int = 192_000
    burn_subtitle_track_id: str | None = None
    advanced: dict[str, Any] = Field(default_factory=dict)


class Project(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str
    root_path: str
    main_sequence_id: str
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)
    workflow_auto_continue: bool | None = None

    @field_validator("name")
    @classmethod
    def non_empty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be empty")
        return value


class AssetFingerprint(DomainModel):
    size: int
    modified_ns: int
    edge_sha256: str


class MediaMetadata(DomainModel):
    duration_frames: int = 0
    width: int | None = None
    height: int | None = None
    fps_numerator: int | None = None
    fps_denominator: int | None = None
    bitrate: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    pixel_format: str | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    variable_frame_rate: bool = False
    has_video: bool = False
    has_audio: bool = False


class Asset(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    name: str
    kind: AssetKind
    origin: AssetOrigin
    path: str
    managed: bool = False
    proxy_path: str | None = None
    sdr_preview_proxy_path: str | None = None
    waveform_path: str | None = None
    status: AssetStatus = AssetStatus.ONLINE
    fingerprint: AssetFingerprint | None = None
    metadata: MediaMetadata = Field(default_factory=MediaMetadata)
    created_at: int = Field(default_factory=now_ms)


class Sequence(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    name: str
    kind: SequenceKind
    profile: ProjectProfile = Field(default_factory=ProjectProfile)
    export_preset: ExportPreset | None = None
    position: int = 0
    created_at: int = Field(default_factory=now_ms)


class TimelineMarker(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str
    frame: int
    name: str = ""
    color: str = "#4ea1ff"

    @field_validator("frame")
    @classmethod
    def non_negative_frame(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Marker frame cannot be negative")
        return value


class TimelineRange(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str
    start_frame: int
    end_frame: int
    name: str = ""
    color: str = "#4ea1ff"

    @model_validator(mode="after")
    def positive_range(self) -> TimelineRange:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("Timeline range must have a positive frame span")
        return self


class Track(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str
    name: str
    kind: TrackKind
    position: int
    enabled: bool = True
    locked: bool = False
    muted: bool = False
    solo: bool = False
    audio_bus_id: str | None = None


class ClipTransform(DomainModel):
    x: float = 0.0
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 0.0
    crop_bottom: float = 0.0
    opacity: float = 1.0

    @field_validator("crop_left", "crop_top", "crop_right", "crop_bottom")
    @classmethod
    def crop_fraction(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("Crop values must be normalized between 0 and 1")
        return value

    @field_validator("opacity")
    @classmethod
    def opacity_fraction(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("Opacity must be normalized between 0 and 1")
        return value


class ClipAudio(DomainModel):
    gain_db: float = 0.0
    pan: float = 0.0
    fade_in_frames: int = 0
    fade_out_frames: int = 0

    @field_validator("pan")
    @classmethod
    def normalized_pan(cls, value: float) -> float:
        if not -1.0 <= value <= 1.0:
            raise ValueError("Pan must be between -1 and 1")
        return value


class Clip(DomainModel):
    id: str = Field(default_factory=new_id)
    track_id: str
    asset_id: str
    timeline_start: int
    source_in: int
    duration: int
    speed_numerator: int = 1
    speed_denominator: int = 1
    pitch_compensation: bool = True
    transform: ClipTransform = Field(default_factory=ClipTransform)
    audio: ClipAudio = Field(default_factory=ClipAudio)

    @model_validator(mode="after")
    def validate_clip(self) -> Clip:
        if self.timeline_start < 0 or self.source_in < 0:
            raise ValueError("Clip frame positions cannot be negative")
        if self.duration <= 0:
            raise ValueError("Clip duration must be positive")
        if self.speed_numerator == 0 or self.speed_denominator <= 0:
            raise ValueError("Clip speed must be non-zero with a positive denominator")
        speed = abs(Fraction(self.speed_numerator, self.speed_denominator))
        if speed < Fraction(1, 4) or speed > 4:
            raise ValueError("Clip speed must be between 0.25x and 4x")
        return self

    @computed_field
    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.duration


class Transition(DomainModel):
    id: str = Field(default_factory=new_id)
    track_id: str
    left_clip_id: str
    right_clip_id: str
    kind: TransitionKind
    duration: int
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("duration")
    @classmethod
    def positive_duration(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Transition duration must be positive")
        return value


class SubtitleSegment(DomainModel):
    id: str = Field(default_factory=new_id)
    document_id: str
    source_segment_id: str | None = None
    start_frame: int
    end_frame: int
    text: str
    speaker: str | None = None
    confidence: float | None = None

    @model_validator(mode="after")
    def validate_times(self) -> SubtitleSegment:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("Subtitle segment must have a positive frame range")
        return self


class SubtitleDocument(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    asset_id: str
    language: str
    source_document_id: str | None = None
    is_source: bool = True
    created_at: int = Field(default_factory=now_ms)


class SubtitlePlacement(DomainModel):
    id: str = Field(default_factory=new_id)
    track_id: str
    segment_id: str
    clip_id: str | None = None
    start_frame: int
    end_frame: int
    text_override: str | None = None


class AudioBus(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str
    name: str
    parent_bus_id: str | None = None
    position: int = 0
    gain_db: float = 0.0
    muted: bool = False
    solo: bool = False
    channel_layout: str = "stereo"

    @field_validator("channel_layout")
    @classmethod
    def valid_channel_layout(cls, value: str) -> str:
        if value not in {"mono", "stereo", "5.1"}:
            raise ValueError("Unsupported channel layout")
        return value


class ParametricEqParameters(DomainModel):
    low_db: float = Field(default=0.0, ge=-24.0, le=24.0)
    low_mid_db: float = Field(default=0.0, ge=-24.0, le=24.0)
    high_mid_db: float = Field(default=0.0, ge=-24.0, le=24.0)
    high_db: float = Field(default=0.0, ge=-24.0, le=24.0)


class HighPassParameters(DomainModel):
    frequency_hz: float = Field(default=80.0, ge=20.0, le=20_000.0)


class LowPassParameters(DomainModel):
    frequency_hz: float = Field(default=16_000.0, ge=20.0, le=24_000.0)


class CompressorParameters(DomainModel):
    threshold_db: float = Field(default=-18.0, ge=-60.0, le=0.0)
    ratio: float = Field(default=3.0, ge=1.0, le=20.0)
    attack_ms: float = Field(default=10.0, ge=0.1, le=2_000.0)
    release_ms: float = Field(default=120.0, ge=10.0, le=5_000.0)


class LimiterParameters(DomainModel):
    ceiling_db: float = Field(default=-1.0, ge=-20.0, le=0.0)


class NoiseGateParameters(DomainModel):
    threshold_db: float = Field(default=-45.0, ge=-80.0, le=0.0)


class RnnoiseParameters(DomainModel):
    mix: float = Field(default=1.0, ge=0.0, le=1.0)


class ChannelMapParameters(DomainModel):
    layout: Literal["mono", "stereo", "5.1"] = "stereo"


class LoudnessNormalizeParameters(DomainModel):
    target_lufs: float = Field(default=-14.0, ge=-30.0, le=-5.0)
    true_peak_db: float = Field(default=-1.0, ge=-9.0, le=0.0)


class DuckingParameters(DomainModel):
    driver_bus_id: str = ""
    threshold_db: float = Field(default=-24.0, ge=-60.0, le=0.0)
    reduction_db: float = Field(default=-10.0, ge=-40.0, le=0.0)
    attack_ms: float = Field(default=120.0, ge=0.0, le=2_000.0)
    release_ms: float = Field(default=300.0, ge=0.0, le=5_000.0)


_AUDIO_EFFECT_PARAMETER_TYPES: dict[AudioEffectKind, type[DomainModel]] = {
    AudioEffectKind.PARAMETRIC_EQ: ParametricEqParameters,
    AudioEffectKind.HIGH_PASS: HighPassParameters,
    AudioEffectKind.LOW_PASS: LowPassParameters,
    AudioEffectKind.COMPRESSOR: CompressorParameters,
    AudioEffectKind.LIMITER: LimiterParameters,
    AudioEffectKind.NOISE_GATE: NoiseGateParameters,
    AudioEffectKind.RNNOISE: RnnoiseParameters,
    AudioEffectKind.CHANNEL_MAP: ChannelMapParameters,
    AudioEffectKind.LOUDNESS_NORMALIZE: LoudnessNormalizeParameters,
    AudioEffectKind.DUCKING: DuckingParameters,
}


def audio_effect_parameter_schema(kind: AudioEffectKind) -> dict[str, dict[str, Any]]:
    return _AUDIO_EFFECT_PARAMETER_TYPES[kind].model_json_schema()["properties"]


class AudioEffect(DomainModel):
    id: str = Field(default_factory=new_id)
    bus_id: str
    kind: AudioEffectKind
    position: int
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_parameters(self) -> AudioEffect:
        parameter_type = _AUDIO_EFFECT_PARAMETER_TYPES[self.kind]
        validated = parameter_type.model_validate(self.parameters)
        object.__setattr__(self, "parameters", validated.model_dump())
        return self


class HighlightCandidate(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    asset_id: str
    start_frame: int
    end_frame: int
    title: str
    reason: str = ""
    score: float = 0.0


class Task(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    sequence_id: str | None = None
    kind: TaskKind
    status: TaskStatus = TaskStatus.PENDING
    name: str
    progress: float = 0.0
    message_code: str = "queued"
    input_asset_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None
    revision: int = 0
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)

    @field_validator("progress")
    @classmethod
    def bounded_progress(cls, value: float) -> float:
        if not 0.0 <= value <= 100.0:
            raise ValueError("Task progress must be between 0 and 100")
        return value


class WorkflowRun(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    sequence_id: str
    asset_ids: list[str] = Field(default_factory=list)
    stage: WorkflowStage
    status: WorkflowStatus
    auto_continue: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    message_code: str = ""
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)


class TimelineState(DomainModel):
    sequence: Sequence
    tracks: list[Track] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list)
    ranges: list[TimelineRange] = Field(default_factory=list)

    def clips_for_track(self, track_id: str) -> list[Clip]:
        return sorted(
            (clip for clip in self.clips if clip.track_id == track_id),
            key=lambda clip: (clip.timeline_start, clip.id),
        )
