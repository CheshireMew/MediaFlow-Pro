from __future__ import annotations

from pydantic import Field

from .model_base import DomainModel


class ColorScopeStatistics(DomainModel):
    average_luma: float = Field(ge=0.0, le=1.0)
    black_clip_percent: float = Field(ge=0.0, le=100.0)
    white_clip_percent: float = Field(ge=0.0, le=100.0)


class ColorScopeAnalysis(DomainModel):
    frame: int = Field(ge=0)
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    bins: int = Field(default=64, ge=16, le=256)
    histogram_red: list[int]
    histogram_green: list[int]
    histogram_blue: list[int]
    histogram_luma: list[int]
    waveform_luma: list[list[int]]
    waveform_red: list[list[int]]
    waveform_green: list[list[int]]
    waveform_blue: list[list[int]]
    vectorscope: list[list[int]]
    statistics: ColorScopeStatistics
