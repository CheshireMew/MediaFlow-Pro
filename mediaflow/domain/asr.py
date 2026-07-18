from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

AsrProgress = Callable[[float, str], None]


@dataclass(frozen=True, slots=True)
class AsrSegment:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class AsrResult:
    language: str
    duration_seconds: float
    segments: tuple[AsrSegment, ...]


class AsrEngine(Protocol):
    def transcribe(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult: ...


class AudioRegionExtractor(Protocol):
    def extract(
        self,
        media_path: str | Path,
        output_path: str | Path,
        *,
        start_seconds: float,
        duration_seconds: float,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Path: ...
