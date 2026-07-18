from __future__ import annotations

import re
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
    TrackKind,
    TransitionKind,
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


class SubtitleStyle(DomainModel):
    font_family: str = "Microsoft YaHei UI"
    font_size: int = Field(default=24, ge=8, le=240)
    font_color: str = "#FFFFFF"
    bold: bool = True
    italic: bool = False
    outline_size: int = Field(default=2, ge=0, le=30)
    shadow_size: int = Field(default=0, ge=0, le=30)
    outline_color: str = "#000000"
    background_enabled: bool = False
    background_color: str = "#000000"
    background_opacity: float = Field(default=0.0, ge=0.0, le=1.0)
    background_padding: int = Field(default=5, ge=0, le=100)
    position_x: float = Field(default=0.5, ge=0.0, le=1.0)
    position_y: float = Field(default=0.88, ge=0.0, le=1.0)
    alignment: Literal["left", "center", "right"] = "center"
    multiline_alignment: Literal["top", "center", "bottom"] = "center"

    @field_validator("font_family")
    @classmethod
    def non_empty_font(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Subtitle font family cannot be empty")
        return value

    @field_validator("font_color", "outline_color", "background_color")
    @classmethod
    def valid_hex_color(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", value):
            raise ValueError("Subtitle colors must use #RRGGBB")
        return value


class WatermarkOverlay(DomainModel):
    enabled: bool = False
    asset_id: str | None = None
    position: Literal["TL", "TC", "TR", "LC", "C", "RC", "BL", "BC", "BR"] = "TR"
    position_x: float | None = Field(default=None, ge=0.0, le=1.0)
    position_y: float | None = Field(default=None, ge=0.0, le=1.0)
    width_ratio: float = Field(default=0.2, ge=0.01, le=1.0)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def enabled_has_asset(self) -> WatermarkOverlay:
        if self.enabled and not self.asset_id:
            raise ValueError("Enabled watermark requires an image asset")
        if (self.position_x is None) != (self.position_y is None):
            raise ValueError("Custom watermark position requires both X and Y")
        return self


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
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    watermark: WatermarkOverlay = Field(default_factory=WatermarkOverlay)
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


class SequenceInOut(DomainModel):
    in_frame: int = Field(ge=0)
    out_frame: int = Field(ge=1)

    @model_validator(mode="after")
    def positive_range(self) -> SequenceInOut:
        if self.out_frame <= self.in_frame:
            raise ValueError("Sequence out point must be after its in point")
        return self


class Sequence(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    name: str
    kind: SequenceKind
    profile: ProjectProfile = Field(default_factory=ProjectProfile)
    export_preset: ExportPreset | None = None
    in_out: SequenceInOut | None = None
    archived: bool = False
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
    media_asset_id: str | None = None
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
    low_db: float = Field(default=0.0, ge=-24.0, le=24.0, json_schema_extra={"step": 0.5, "unit": "dB"})
    low_mid_db: float = Field(default=0.0, ge=-24.0, le=24.0, json_schema_extra={"step": 0.5, "unit": "dB"})
    high_mid_db: float = Field(default=0.0, ge=-24.0, le=24.0, json_schema_extra={"step": 0.5, "unit": "dB"})
    high_db: float = Field(default=0.0, ge=-24.0, le=24.0, json_schema_extra={"step": 0.5, "unit": "dB"})


class HighPassParameters(DomainModel):
    frequency_hz: float = Field(
        default=80.0, ge=20.0, le=20_000.0, json_schema_extra={"step": 10.0, "unit": "Hz"}
    )


class LowPassParameters(DomainModel):
    frequency_hz: float = Field(
        default=16_000.0,
        ge=20.0,
        le=24_000.0,
        json_schema_extra={"step": 10.0, "unit": "Hz"},
    )


class CompressorParameters(DomainModel):
    threshold_db: float = Field(
        default=-18.0, ge=-60.0, le=0.0, json_schema_extra={"step": 0.5, "unit": "dB"}
    )
    ratio: float = Field(default=3.0, ge=1.0, le=20.0, json_schema_extra={"step": 0.1, "unit": ":1"})
    attack_ms: float = Field(default=10.0, ge=0.1, le=2_000.0, json_schema_extra={"step": 1.0, "unit": "ms"})
    release_ms: float = Field(
        default=120.0,
        ge=10.0,
        le=5_000.0,
        json_schema_extra={"step": 5.0, "unit": "ms"},
    )


class LimiterParameters(DomainModel):
    ceiling_db: float = Field(default=-1.0, ge=-20.0, le=0.0, json_schema_extra={"step": 0.1, "unit": "dB"})


class NoiseGateParameters(DomainModel):
    threshold_db: float = Field(
        default=-45.0, ge=-80.0, le=0.0, json_schema_extra={"step": 0.5, "unit": "dB"}
    )


class RnnoiseParameters(DomainModel):
    mix: float = Field(default=1.0, ge=0.0, le=1.0, json_schema_extra={"step": 0.05, "unit": ""})


class ChannelMapParameters(DomainModel):
    layout: Literal["mono", "stereo", "5.1"] = Field(
        default="stereo",
        json_schema_extra={"step": 0.0, "unit": "", "value_type": "layout"},
    )


class LoudnessNormalizeParameters(DomainModel):
    target_lufs: float = Field(
        default=-14.0, ge=-30.0, le=-5.0, json_schema_extra={"step": 0.5, "unit": "LUFS"}
    )
    true_peak_db: float = Field(
        default=-1.0, ge=-9.0, le=0.0, json_schema_extra={"step": 0.1, "unit": "dBTP"}
    )


class DuckingParameters(DomainModel):
    driver_bus_id: str = Field(
        default="",
        json_schema_extra={"step": 0.0, "unit": "", "value_type": "bus"},
    )
    threshold_db: float = Field(
        default=-24.0, ge=-60.0, le=0.0, json_schema_extra={"step": 0.5, "unit": "dB"}
    )
    reduction_db: float = Field(
        default=-10.0, ge=-40.0, le=0.0, json_schema_extra={"step": 0.5, "unit": "dB"}
    )
    attack_ms: float = Field(default=120.0, ge=0.0, le=2_000.0, json_schema_extra={"step": 5.0, "unit": "ms"})
    release_ms: float = Field(
        default=300.0, ge=0.0, le=5_000.0, json_schema_extra={"step": 5.0, "unit": "ms"}
    )


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
    document_id: str | None = None
    sequence_id: str | None = None
    start_frame: int
    end_frame: int
    title: str
    reason: str = ""
    score: float = 0.0
    selected: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> HighlightCandidate:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("Highlight candidate must have a positive frame range")
        if not self.title.strip():
            raise ValueError("Highlight candidate title cannot be empty")
        return self


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
