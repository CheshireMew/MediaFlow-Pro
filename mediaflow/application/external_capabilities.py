from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mediaflow.domain.reference_comparison import (
    ReferenceComparisonAcceptance,
    ReferenceComparisonResult,
)
from mediaflow.domain.runtime_capabilities import RuntimeInspection
from mediaflow.domain.speech import (
    SpeechSynthesisResult,
    SpeechSynthesizeArguments,
    SpeechTranscribeArguments,
    SpeechTranscriptionResult,
)


class ReferenceComparisonCapability(Protocol):
    def compare(
        self,
        *,
        reference_path: str | Path,
        candidate_path: str | Path,
        output_dir: str | Path,
        reference_start_frame: int = 0,
        candidate_start_frame: int = 0,
        frame_count: int | None = None,
        temporal_search_radius_frames: int = 0,
        boundary_frame_count: int = 3,
        contact_sheet_rows: int = 8,
        acceptance: ReferenceComparisonAcceptance | None = None,
        overwrite: bool = False,
    ) -> ReferenceComparisonResult: ...


class RuntimeInspectionCapability(Protocol):
    def inspect(self) -> RuntimeInspection: ...


class SpeechCapability(Protocol):
    def transcribe(
        self,
        request: SpeechTranscribeArguments,
    ) -> SpeechTranscriptionResult: ...

    def synthesize(
        self,
        request: SpeechSynthesizeArguments,
    ) -> SpeechSynthesisResult: ...
