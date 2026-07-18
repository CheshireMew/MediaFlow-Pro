from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .enums import ExportFormat
from .model_base import DomainModel, new_id


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
