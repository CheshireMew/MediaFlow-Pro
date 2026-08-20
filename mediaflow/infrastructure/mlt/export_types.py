from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.timeline import TimelineState


@dataclass(frozen=True, slots=True)
class ExportResult:
    output_path: Path
    project_graph_path: Path
    probe: dict
    subtitle_files: tuple[Path, ...] = ()
    start_frame: int = 0
    end_frame: int = 1
    requested_video_codec: str | None = None
    actual_video_codec: str | None = None
    hardware_fallback_reason: str | None = None
    hardware_failure_details: str | None = None
    archived_failed_outputs: tuple[Path, ...] = ()

    @property
    def hardware_fallback_used(self) -> bool:
        return self.hardware_fallback_reason is not None


@dataclass(frozen=True, slots=True)
class ExternalSubtitleOutput:
    destination: Path
    content: str


@dataclass(frozen=True, slots=True)
class MltExportRequest:
    state: TimelineState
    preset: ExportPreset
    output_path: str | Path
    start_frame: int | None = None
    end_frame: int | None = None


class RuntimeExportPreset(ExportPreset):
    video_codec: str | None


@dataclass(frozen=True, slots=True)
class MltExportPlan:
    output: Path
    preset: RuntimeExportPreset
    subtitle_outputs: tuple[ExternalSubtitleOutput, ...]
    start_frame: int
    end_frame: int

    @property
    def destinations(self) -> tuple[Path, ...]:
        return self.output, *(item.destination for item in self.subtitle_outputs)
