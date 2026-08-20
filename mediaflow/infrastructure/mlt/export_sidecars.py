from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mediaflow.domain.enums import TrackKind
from mediaflow.domain.srt_time import format_srt_timestamp
from mediaflow.domain.storage_names import (
    WINDOWS_COMPONENT_UTF16_LIMIT,
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    safe_path_component,
    utf16_units,
)
from mediaflow.domain.timebase import frames_to_seconds
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.output_reservation import (
    OutputSetTransaction,
    require_output_transaction_path,
)

from .compiler import TimelineCompiler
from .export_types import ExternalSubtitleOutput

_SIDECAR_COMPONENT_UTF16_BUDGET = 240
_SIDECAR_OUTPUT_STEM_UTF16_BUDGET = 144


class MltSubtitleSidecars:
    def __init__(self, compiler: TimelineCompiler):
        self.compiler = compiler

    def plan(
        self,
        state: TimelineState,
        output: Path,
        *,
        start_frame: int,
        end_frame: int,
    ) -> tuple[ExternalSubtitleOutput, ...]:
        segments = {
            segment.id: segment
            for document in self.compiler.repository.subtitles.list_subtitle_documents()
            for segment in self.compiler.repository.subtitles.list_subtitle_segments(document.id)
        }
        planned: list[ExternalSubtitleOutput] = []
        used_destination_keys: set[str] = set()
        for track in state.tracks:
            if track.kind != TrackKind.SUBTITLE or not track.enabled:
                continue
            placements = self.compiler.repository.subtitles.list_subtitle_placements(track.id)
            included = [
                placement
                for placement in placements
                if placement.end_frame > start_frame and placement.start_frame < end_frame
            ]
            if not included:
                continue
            destination = _subtitle_sidecar_destination(output, track.name)
            destination_key = os.path.normcase(str(destination))
            if destination_key in used_destination_keys:
                destination = _subtitle_sidecar_destination(
                    output,
                    track.name,
                    collision_suffix=hashlib.sha256(track.id.encode("utf-8")).hexdigest()[:12],
                )
                destination_key = os.path.normcase(str(destination))
            if destination_key in used_destination_keys:
                raise RuntimeError(f"Subtitle tracks resolve to the same sidecar path: {destination}")
            used_destination_keys.add(destination_key)
            lines: list[str] = []
            for index, placement in enumerate(included, start=1):
                segment = segments.get(placement.segment_id)
                if segment is None:
                    raise RuntimeError("Subtitle export references a missing segment")
                start = frames_to_seconds(
                    max(start_frame, placement.start_frame) - start_frame,
                    state.sequence.profile.fps_numerator,
                    state.sequence.profile.fps_denominator,
                )
                end = frames_to_seconds(
                    min(end_frame, placement.end_frame) - start_frame,
                    state.sequence.profile.fps_numerator,
                    state.sequence.profile.fps_denominator,
                )
                lines.extend(
                    [
                        str(index),
                        f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}",
                        placement.text_override or segment.text,
                        "",
                    ]
                )
            planned.append(
                ExternalSubtitleOutput(
                    destination=destination,
                    content="\n".join(lines),
                )
            )
        return tuple(planned)

    @staticmethod
    def stage(
        subtitle_outputs: tuple[ExternalSubtitleOutput, ...],
        output_set: OutputSetTransaction,
    ) -> tuple[Path, ...]:
        generated: list[Path] = []
        for subtitle in subtitle_outputs:
            temporary = output_set.temporary_path(subtitle.destination, "subtitle")
            try:
                temporary.write_text(subtitle.content, encoding="utf-8-sig")
                if temporary.read_text(encoding="utf-8-sig") != subtitle.content:
                    raise RuntimeError(f"External subtitle verification failed: {subtitle.destination}")
            except Exception:
                output_set.archive_staged(subtitle.destination)
                raise
            generated.append(subtitle.destination)
        return tuple(generated)


def _subtitle_sidecar_destination(
    output: Path,
    track_name: str,
    *,
    collision_suffix: str | None = None,
) -> Path:
    component_budget = min(
        _SIDECAR_COMPONENT_UTF16_BUDGET,
        WINDOWS_COMPONENT_UTF16_LIMIT,
        WINDOWS_INTEROP_PATH_UTF16_LIMIT - utf16_units(str(output.parent.resolve())) - 1,
    )
    suffix = f"-{collision_suffix}" if collision_suffix else ""
    fixed_units = utf16_units(suffix) + utf16_units("..srt")
    output_stem_budget = min(
        _SIDECAR_OUTPUT_STEM_UTF16_BUDGET,
        component_budget - fixed_units - 8,
    )
    if output_stem_budget < 8:
        raise ValueError("导出目录过深，无法生成外置字幕文件名")
    safe_output_stem = safe_path_component(
        output.stem,
        fallback="export",
        max_utf16_units=output_stem_budget,
    )
    track_budget = component_budget - utf16_units(safe_output_stem) - fixed_units
    safe_track_name = safe_path_component(
        track_name,
        fallback="subtitle",
        max_utf16_units=track_budget,
    )
    component = f"{safe_output_stem}.{safe_track_name}{suffix}.srt"
    if utf16_units(component) > component_budget:
        raise RuntimeError("Subtitle sidecar filename exceeds its storage budget")
    return require_output_transaction_path(output.with_name(component))
