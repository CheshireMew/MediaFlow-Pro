from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .enums import ExportFormat
from .model_base import DomainModel, new_id

EncoderPolicyMode = Literal["software", "prefer_hardware"]
EncoderVendor = Literal["auto", "nvidia", "intel", "amd", "apple"]

_CONTAINER_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "flac": ("flac",),
    "ipod": ("m4a",),
    "matroska": ("mkv",),
    "mkv": ("mkv",),
    "mov": ("mov",),
    "mp4": ("mp4", "m4v"),
    "mpegts": ("ts", "m2ts"),
    "ogg": ("ogg", "oga", "opus"),
    "wav": ("wav",),
    "webm": ("webm",),
}
_INTEGER_ADVANCED_FIELDS = frozenset(
    {
        "width",
        "height",
        "fps_numerator",
        "fps_denominator",
        "audio_sample_rate",
        "audio_channels",
    }
)


class SubtitleStyle(DomainModel):
    font_family: str = "LXGW WenKai"
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


class VideoEncoderPolicy(DomainModel):
    mode: EncoderPolicyMode = "software"
    vendor: EncoderVendor = "auto"

    @model_validator(mode="after")
    def coherent_vendor(self) -> VideoEncoderPolicy:
        if self.mode == "software" and self.vendor != "auto":
            raise ValueError("Software encoder policy must use the automatic vendor")
        return self


class ExportPreset(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str
    format: ExportFormat
    container: str
    encoder_policy: VideoEncoderPolicy | None
    audio_codec: str | None
    pixel_format: str | None
    quality_mode: Literal["crf"] = "crf"
    quality_value: float = Field(
        default=18.0,
        ge=0.0,
        le=63.0,
        allow_inf_nan=False,
    )
    preset: str = "medium"
    gop_frames: int = Field(default=60, ge=1)
    audio_bitrate: int = Field(default=192_000, gt=0)
    burn_subtitle_track_id: str | None = None
    subtitle_style: SubtitleStyle | None = None
    watermark: WatermarkOverlay = Field(default_factory=WatermarkOverlay)
    advanced: dict[str, Any] = Field(default_factory=dict)

    @field_validator("advanced", mode="before")
    @classmethod
    def normalize_integral_advanced_values(
        cls,
        value: object,
    ) -> object:
        """Normalize lossless numbers crossing JSON/QVariant boundaries."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for key in _INTEGER_ADVANCED_FIELDS:
            item = normalized.get(key)
            if isinstance(item, float) and item.is_integer():
                normalized[key] = int(item)
        return normalized

    @field_validator("name", "container", "preset")
    @classmethod
    def required_export_text(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Export preset text fields cannot be empty")
        return normalized

    @field_validator(
        "audio_codec",
        "pixel_format",
        "burn_subtitle_track_id",
    )
    @classmethod
    def optional_export_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Optional export preset text cannot be blank")
        return normalized

    @field_validator("container")
    @classmethod
    def valid_container(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_]+", value) is None:
            raise ValueError("Export container must be an FFmpeg muxer name")
        return value.casefold()

    @model_validator(mode="after")
    def coherent_media_format(self) -> ExportPreset:
        if self.format == ExportFormat.AUDIO:
            if self.encoder_policy is not None:
                raise ValueError("Audio-only export cannot use a video encoder policy")
            if self.pixel_format is not None:
                raise ValueError("Audio-only export cannot use a pixel format")
            if self.audio_codec is None:
                raise ValueError("Audio-only export requires an audio codec")
        elif self.encoder_policy is None:
            raise ValueError("Video export requires a video encoder policy")
        for key in _INTEGER_ADVANCED_FIELDS:
            value = self.advanced.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"Advanced export field {key} must be a positive integer")
        return self

    @property
    def preferred_extension(self) -> str:
        extensions = _CONTAINER_EXTENSIONS.get(self.container)
        return extensions[0] if extensions else self.container

    def validate_destination(self, destination: str | Path) -> Path:
        output = Path(destination)
        extension = output.suffix.removeprefix(".").casefold()
        if not extension:
            raise ValueError("导出目标必须包含文件扩展名")
        expected = _CONTAINER_EXTENSIONS.get(self.container)
        if expected is not None and extension not in expected:
            allowed = "、".join(f".{item}" for item in expected)
            raise ValueError(f"导出文件扩展名与封装格式不一致：{self.container} 应使用 {allowed}")
        return output
