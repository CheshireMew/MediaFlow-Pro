from __future__ import annotations

from typing import Literal

from pydantic import Field

from .model_base import DomainModel


class StockMediaItem(DomainModel):
    id: str
    provider: Literal["pexels", "pixabay", "unsplash"]
    kind: Literal["video", "image"]
    title: str
    preview_url: str
    download_url: str
    source_url: str
    attribution: str
    attribution_url: str = ""
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    filename: str
    tracking_url: str = ""
