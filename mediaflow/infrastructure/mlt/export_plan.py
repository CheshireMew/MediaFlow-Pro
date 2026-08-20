from __future__ import annotations

from pathlib import Path

from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.encoder_catalog import VIDEO_ENCODERS
from mediaflow.infrastructure.output_reservation import (
    OutputSetTransaction,
    require_output_transaction_path,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths

from .export_encoder import MltExportEncoder
from .export_sidecars import MltSubtitleSidecars
from .export_types import MltExportPlan, MltExportRequest, RuntimeExportPreset


class MltExportPlanner:
    def __init__(
        self,
        paths: RuntimePaths,
        encoder: MltExportEncoder,
        sidecars: MltSubtitleSidecars,
    ):
        self.paths = paths
        self.encoder = encoder
        self.sidecars = sidecars

    def plans_for_requests(
        self,
        requests: tuple[MltExportRequest, ...],
        *,
        overwrite: bool,
    ) -> tuple[MltExportPlan, ...]:
        if not requests:
            raise ValueError("At least one sequence must be exported")
        plans = tuple(
            self.plan(
                request.state,
                self.encoder.resolve_preset(request.preset),
                request.output_path,
                start_frame=request.start_frame,
                end_frame=request.end_frame,
            )
            for request in requests
        )
        preflight = OutputSetTransaction(
            (destination for plan in plans for destination in plan.destinations),
            overwrite=overwrite,
        )
        preflight.check_conflicts()
        return plans

    def plan(
        self,
        state: TimelineState,
        preset: RuntimeExportPreset,
        output_path: str | Path,
        *,
        start_frame: int | None = None,
        end_frame: int | None = None,
    ) -> MltExportPlan:
        if self.paths.melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        encoder = VIDEO_ENCODERS.get(str(preset.video_codec or ""))
        if encoder is not None and encoder.format != preset.format:
            raise ValueError(
                "Export video codec does not match its format: "
                f"{preset.video_codec} is {encoder.format.value}, not {preset.format.value}"
            )
        output = require_output_transaction_path(preset.validate_destination(output_path))
        start_frame, end_frame = resolve_export_range(
            state,
            start_frame=start_frame,
            end_frame=end_frame,
        )
        return MltExportPlan(
            output=output,
            preset=preset,
            subtitle_outputs=self.sidecars.plan(
                state,
                output,
                start_frame=start_frame,
                end_frame=end_frame,
            ),
            start_frame=start_frame,
            end_frame=end_frame,
        )


def resolve_export_range(
    state: TimelineState,
    *,
    start_frame: int | None = None,
    end_frame: int | None = None,
) -> tuple[int, int]:
    duration = max(1, state.duration_frames)
    if (start_frame is None) != (end_frame is None):
        raise ValueError("Explicit export range requires both start_frame and end_frame")
    if start_frame is not None and end_frame is not None:
        start = int(start_frame)
        end = int(end_frame)
        if start < 0 or end > duration:
            raise ValueError(f"Explicit export range {start}:{end} exceeds timeline duration {duration}")
    else:
        bounds = state.sequence.in_out
        start = min(duration, bounds.in_frame) if bounds else 0
        end = min(duration, bounds.out_frame) if bounds else duration
    if end <= start:
        raise ValueError("Sequence in and out points do not contain exportable media")
    return start, end
