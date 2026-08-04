from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from mediaflow.domain.enums import ColorMode, ExportFormat, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.srt_time import format_srt_timestamp
from mediaflow.domain.storage_names import (
    WINDOWS_COMPONENT_UTF16_LIMIT,
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    safe_path_component,
    utf16_units,
)
from mediaflow.domain.timebase import frames_to_seconds
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.encoder_catalog import (
    VIDEO_ENCODERS,
    codec_backend,
    is_hardware_codec,
    software_encoder_for_format,
)
from mediaflow.infrastructure.encoder_policy import VideoEncoderPolicyResolver
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.font_assets import apply_bundled_font_environment
from mediaflow.infrastructure.output_reservation import (
    OutputSetTransaction,
    output_set_transaction,
    require_output_transaction_path,
)
from mediaflow.infrastructure.process_observers import MeltProgressObserver
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import (
    run_cancellable,
    run_cancellable_streaming,
)

from .compiler import TimelineCompiler


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


class _RuntimeExportPreset(ExportPreset):
    video_codec: str | None


@dataclass(frozen=True, slots=True)
class _MltExportPlan:
    output: Path
    preset: _RuntimeExportPreset
    subtitle_outputs: tuple[ExternalSubtitleOutput, ...]
    start_frame: int
    end_frame: int

    @property
    def destinations(self) -> tuple[Path, ...]:
        return (
            self.output,
            *(item.destination for item in self.subtitle_outputs),
        )


_SIDECAR_COMPONENT_UTF16_BUDGET = 240
_SIDECAR_OUTPUT_STEM_UTF16_BUDGET = 144


def _subtitle_sidecar_destination(
    output: Path,
    track_name: str,
    *,
    collision_suffix: str | None = None,
) -> Path:
    component_budget = min(
        _SIDECAR_COMPONENT_UTF16_BUDGET,
        WINDOWS_COMPONENT_UTF16_LIMIT,
        WINDOWS_INTEROP_PATH_UTF16_LIMIT
        - utf16_units(str(output.parent.resolve()))
        - 1,
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
    track_budget = (
        component_budget
        - utf16_units(safe_output_stem)
        - utf16_units(suffix)
        - utf16_units("..srt")
    )
    safe_track_name = safe_path_component(
        track_name,
        fallback="subtitle",
        max_utf16_units=track_budget,
    )
    component = f"{safe_output_stem}.{safe_track_name}{suffix}.srt"
    if utf16_units(component) > component_budget:
        raise RuntimeError("Subtitle sidecar filename exceeds its storage budget")
    return require_output_transaction_path(
        output.with_name(component)
    )


class ExportAttemptError(RuntimeError):
    def __init__(
        self,
        diagnostic: str,
        *,
        archived_output: Path | None,
    ) -> None:
        super().__init__("MLT export failed:\n" + diagnostic)
        self.diagnostic = diagnostic
        self.archived_output = archived_output


class MltExportService:
    def __init__(
        self,
        compiler: TimelineCompiler,
        paths: RuntimePaths,
        encoder_resolver: VideoEncoderPolicyResolver | None = None,
    ):
        self.compiler = compiler
        self.paths = paths
        self.encoder_resolver = encoder_resolver or VideoEncoderPolicyResolver(self.paths)

    def export(
        self,
        state: TimelineState,
        preset: ExportPreset,
        output_path: str | Path,
        *,
        overwrite: bool = False,
        archive_replaced_to: str | Path | None = None,
        progress=None,
        check_cancelled=None,
    ) -> ExportResult:
        return self.export_many(
            (
                MltExportRequest(
                    state=state,
                    preset=preset,
                    output_path=output_path,
                ),
            ),
            overwrite=overwrite,
            archive_replaced_to=archive_replaced_to,
            progress=progress,
            check_cancelled=check_cancelled,
        )[0]

    def export_many(
        self,
        requests: tuple[MltExportRequest, ...],
        *,
        overwrite: bool = False,
        archive_replaced_to: str | Path | None = None,
        progress=None,
        check_cancelled=None,
    ) -> tuple[ExportResult, ...]:
        request_items = tuple(requests)
        plans = self._plans_for_requests(
            request_items,
            overwrite=overwrite,
        )
        with output_set_transaction(
            (
                destination
                for plan in plans
                for destination in plan.destinations
            ),
            overwrite=overwrite,
            runtime_dir=self.paths.runtime_dir,
        ) as output_set:
            graph_paths: list[Path] = []
            for request, plan in zip(
                request_items,
                plans,
                strict=True,
            ):
                if check_cancelled:
                    check_cancelled()
                graph_paths.append(
                    self._compile_graph(
                        request.state,
                        plan.preset,
                        plan.output,
                        progress=progress,
                    )
                )
            for plan in plans:
                for destination in plan.destinations:
                    destination.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
            results: list[ExportResult] = []
            for index, (request, plan, graph_path) in enumerate(
                zip(
                    request_items,
                    plans,
                    graph_paths,
                    strict=True,
                ),
                start=1,
            ):
                if check_cancelled:
                    check_cancelled()
                results.append(
                    self._stage_reserved(
                        request.state,
                        plan.preset,
                        plan.output,
                        graph_path=graph_path,
                        output_set=output_set,
                        subtitle_outputs=plan.subtitle_outputs,
                        start_frame=plan.start_frame,
                        end_frame=plan.end_frame,
                        progress=progress,
                        check_cancelled=check_cancelled,
                    )
                )
                if progress and len(request_items) > 1:
                    progress(
                        OperationProgress.determinate(
                            "clip_export_items",
                            completed=index,
                            total=len(request_items),
                            unit="items",
                        )
                    )
            if check_cancelled:
                check_cancelled()
            output_set.publish()
            output_set.finalize(archive_replaced_to=archive_replaced_to)
            return tuple(results)

    def preflight(
        self,
        state: TimelineState,
        preset: ExportPreset,
        output_path: str | Path,
        *,
        overwrite: bool = False,
    ) -> None:
        """Validate the complete output set without rendering or creating paths."""

        self.preflight_many(
            (
                MltExportRequest(
                    state=state,
                    preset=preset,
                    output_path=output_path,
                ),
            ),
            overwrite=overwrite,
        )

    def preflight_many(
        self,
        requests: tuple[MltExportRequest, ...],
        *,
        overwrite: bool = False,
    ) -> None:
        """Validate an atomic batch without rendering or creating paths."""

        self._plans_for_requests(
            tuple(requests),
            overwrite=overwrite,
        )

    def _plans_for_requests(
        self,
        requests: tuple[MltExportRequest, ...],
        *,
        overwrite: bool,
    ) -> tuple[_MltExportPlan, ...]:
        if not requests:
            raise ValueError("At least one sequence must be exported")
        plans = tuple(
            self._export_plan(
                request.state,
                self._resolve_runtime_preset(request.preset),
                request.output_path,
                start_frame=request.start_frame,
                end_frame=request.end_frame,
            )
            for request in requests
        )
        preflight = OutputSetTransaction(
            (
                destination
                for plan in plans
                for destination in plan.destinations
            ),
            overwrite=overwrite,
        )
        preflight.check_conflicts()
        return plans

    def _export_plan(
        self,
        state: TimelineState,
        preset: _RuntimeExportPreset,
        output_path: str | Path,
        *,
        start_frame: int | None = None,
        end_frame: int | None = None,
    ) -> _MltExportPlan:
        if self.paths.melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        encoder = VIDEO_ENCODERS.get(
            str(preset.video_codec or "")
        )
        if (
            encoder is not None
            and encoder.format != preset.format
        ):
            raise ValueError(
                "Export video codec does not match its format: "
                f"{preset.video_codec} is {encoder.format.value}, "
                f"not {preset.format.value}"
            )
        output = require_output_transaction_path(
            preset.validate_destination(output_path)
        )
        start_frame, end_frame = self._resolve_export_range(
            state,
            preset,
            start_frame=start_frame,
            end_frame=end_frame,
        )
        subtitle_outputs = self._external_subtitle_outputs(
            state,
            output,
            start_frame=start_frame,
            end_frame=end_frame,
        )
        return _MltExportPlan(
            output=output,
            preset=preset,
            subtitle_outputs=subtitle_outputs,
            start_frame=start_frame,
            end_frame=end_frame,
        )

    def _resolve_runtime_preset(self, preset: ExportPreset) -> _RuntimeExportPreset:
        if preset.format == ExportFormat.AUDIO:
            codec = None
        else:
            policy = preset.encoder_policy
            if policy is None:
                raise ValueError("Video export requires a video encoder policy")
            codec = self.encoder_resolver.resolve(preset.format, policy).codec
        return _RuntimeExportPreset.model_validate(
            {
                **preset.model_dump(mode="python"),
                "video_codec": codec,
            }
        )

    def execution_identity(self, preset: ExportPreset) -> dict[str, object]:
        """Describe every machine-local input that can change encoded bytes."""

        runtime_preset = self._resolve_runtime_preset(preset)

        def binary(path: Path | None) -> dict[str, object] | None:
            if path is None or not path.is_file():
                return None
            value = fingerprint_file(path)
            return {
                "name": path.name,
                "size": value.size,
                "modified_ns": value.modified_ns,
                "edge_sha256": value.edge_sha256,
            }

        return {
            "target": self.paths.target.key,
            "video_codec": runtime_preset.video_codec,
            "melt": binary(self.paths.melt),
            "ffmpeg": binary(self.paths.ffmpeg),
            "ffprobe": binary(self.paths.ffprobe),
        }

    def _compile_graph(
        self,
        state: TimelineState,
        preset: _RuntimeExportPreset,
        output: Path,
        *,
        progress=None,
    ) -> Path:
        graph_key = hashlib.sha256(
            os.path.normcase(str(output)).encode("utf-8")
        ).hexdigest()[:12]
        graph_path = (
            self.compiler.repository.project_dir
            / "cache"
            / "mlt"
            / f"{state.sequence.id}-export-{graph_key}.mlt"
        )
        if progress:
            progress(OperationProgress.indeterminate("export_compiling"))
        self.compiler.write(
            state,
            graph_path,
            use_proxies=False,
            subtitle_track_id=preset.burn_subtitle_track_id,
            subtitle_style=preset.subtitle_style,
            watermark=preset.watermark,
        )
        return graph_path

    def _stage_reserved(
        self,
        state: TimelineState,
        preset: _RuntimeExportPreset,
        output: Path,
        *,
        graph_path: Path,
        output_set: OutputSetTransaction,
        subtitle_outputs: tuple[ExternalSubtitleOutput, ...],
        start_frame: int,
        end_frame: int,
        progress=None,
        check_cancelled=None,
    ) -> ExportResult:
        requested_codec = self._resolved_video_codec(preset)
        archived_outputs: list[Path] = []
        hardware_fallback_reason: str | None = None
        hardware_failure_details: str | None = None
        try:
            probe = self._render_attempt(
                state,
                preset,
                graph_path,
                output,
                output_set,
                start_frame=start_frame,
                end_frame=end_frame,
                attempt_label="requested",
                progress=progress,
                check_cancelled=check_cancelled,
            )
            actual_codec = requested_codec
        except ExportAttemptError as failure:
            reason = self._hardware_failure_reason(requested_codec, failure.diagnostic)
            fallback = software_encoder_for_format(preset.format)
            if (
                not is_hardware_codec(requested_codec)
                or preset.encoder_policy is None
                or preset.encoder_policy.mode != "prefer_hardware"
                or reason is None
                or fallback is None
            ):
                raise
            if failure.archived_output is not None:
                archived_outputs.append(failure.archived_output)
            hardware_fallback_reason = reason
            hardware_failure_details = self._diagnostic_tail(failure.diagnostic)
            if progress:
                progress(OperationProgress.indeterminate("export_hardware_encoder_fallback"))
            fallback_preset = preset.model_copy(
                update={
                    "video_codec": fallback.codec,
                    "preset": fallback.preset,
                }
            )
            try:
                probe = self._render_attempt(
                    state,
                    fallback_preset,
                    graph_path,
                    output,
                    output_set,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    attempt_label="software-fallback",
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            except ExportAttemptError as fallback_failure:
                if fallback_failure.archived_output is not None:
                    archived_outputs.append(fallback_failure.archived_output)
                raise RuntimeError(
                    "Hardware encoding failed and the software recovery attempt also failed.\n"
                    f"Hardware attempt:\n{self._diagnostic_tail(failure.diagnostic)}\n"
                    f"Software attempt:\n{self._diagnostic_tail(fallback_failure.diagnostic)}"
                ) from fallback_failure
            actual_codec = fallback.codec
        subtitle_files = self._stage_external_subtitles(
            subtitle_outputs,
            output_set,
        )
        return ExportResult(
            output_path=output,
            project_graph_path=graph_path,
            probe=probe,
            subtitle_files=subtitle_files,
            start_frame=start_frame,
            end_frame=end_frame,
            requested_video_codec=requested_codec,
            actual_video_codec=actual_codec,
            hardware_fallback_reason=hardware_fallback_reason,
            hardware_failure_details=hardware_failure_details,
            archived_failed_outputs=tuple(archived_outputs),
        )

    def _render_attempt(
        self,
        state: TimelineState,
        preset: _RuntimeExportPreset,
        graph_path: Path,
        output: Path,
        output_set: OutputSetTransaction,
        *,
        start_frame: int,
        end_frame: int,
        attempt_label: str,
        progress=None,
        check_cancelled=None,
    ) -> dict:
        melt = self.paths.melt
        if melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        attempt_output = output_set.temporary_path(
            output,
            attempt_label,
        )
        consumer = self._consumer_properties(state, preset)
        command = [
            str(melt),
            "-progress2",
            str(graph_path),
            f"in={start_frame}",
            f"out={end_frame - 1}",
            "-consumer",
            f"avformat:{attempt_output}",
            *consumer,
            "terminate_on_pause=1",
            "real_time=-1",
        ]
        mlt_root = self.paths.require_mlt_root()
        environment = self.paths.mlt_environment()
        apply_bundled_font_environment(
            preset.subtitle_style.font_family if preset.subtitle_style else None,
            environment,
        )
        total_frames = end_frame - start_frame
        observer = MeltProgressObserver(
            total_frames,
            lambda frame: progress(
                OperationProgress.determinate(
                    "export_rendering",
                    completed=frame,
                    total=total_frames,
                    unit="frames",
                )
            )
            if progress
            else None,
        )
        try:
            result = run_cancellable_streaming(
                command,
                cwd=mlt_root,
                env=environment,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
                check_cancelled=check_cancelled,
                on_stdout_line=observer,
                on_stderr_line=observer,
                split_carriage_returns=True,
            )
        except Exception:
            output_set.archive_staged(output)
            raise
        if (
            result.returncode != 0
            or not attempt_output.is_file()
            or attempt_output.stat().st_size == 0
        ):
            diagnostic = "\n".join(
                part for part in (result.stdout, result.stderr) if part
            ).strip()
            if not diagnostic:
                diagnostic = (
                    f"melt exited with code {result.returncode}; "
                    "the export attempt did not produce a non-empty file"
                )
            archived = output_set.archive_staged(output)
            raise ExportAttemptError(diagnostic, archived_output=archived)
        if progress:
            progress(
                OperationProgress.determinate(
                    "export_rendering",
                    completed=total_frames,
                    total=total_frames,
                    unit="frames",
                )
            )
        if progress:
            progress(OperationProgress.indeterminate("export_verifying"))
        try:
            probe = self.probe(attempt_output)
            self.validate_probe(
                state,
                preset,
                probe,
                expected_duration_frames=end_frame - start_frame,
            )
        except Exception:
            output_set.archive_staged(output)
            raise
        return probe

    @staticmethod
    def _resolved_video_codec(preset: _RuntimeExportPreset) -> str | None:
        return preset.video_codec

    @staticmethod
    def _diagnostic_tail(diagnostic: str, limit: int = 4000) -> str:
        normalized = diagnostic.strip()
        return normalized[-limit:] if len(normalized) > limit else normalized

    @staticmethod
    def _hardware_failure_reason(codec: str | None, diagnostic: str) -> str | None:
        backend = codec_backend(codec)
        if backend not in {"nvenc", "qsv", "amf", "videotoolbox", "vaapi"}:
            return None
        normalized = diagnostic.lower()
        backend_patterns = {
            "nvenc": (
                r"no nvenc capable devices",
                r"cannot load (?:nvcuda|nvencodeapi)",
                r"failed to load (?:nvcuda|nvencodeapi)",
                r"openencodesession",
                r"nvenc.*(?:driver|device|initializ|not available|not supported|unsupported)",
                r"cuda_error",
            ),
            "qsv": (
                r"mfx_err",
                r"(?:qsv|quick sync).*(?:device|session|initializ|not available|not supported|unsupported)",
                r"(?:create|initialize|initializing).*(?:mfx|qsv).*session",
                r"cannot load libmfx",
            ),
            "amf": (
                r"amf_(?:not_supported|no_device|fail)",
                r"(?:amf|amfrt64).*(?:device|initializ|not available|not supported|unsupported|failed)",
            ),
            "videotoolbox": (
                r"videotoolbox.*(?:not available|not supported|failed|error)",
                r"cannot create.*videotoolbox",
            ),
            "vaapi": (
                r"vaapi.*(?:device|initializ|not available|not supported|failed|error)",
                r"failed to initialise vaapi",
                r"no va display found",
            ),
        }
        generic_encoder_patterns = (
            r"unknown encoder",
            r"encoder .* not found",
            r"error (?:initializing|while opening).*encoder",
            r"failed to (?:open|initialize|initialise|create).*encoder",
            r"(?:could not|unable to) open (?:video )?(?:encoder|codec)",
            r"avcodec_open2 failed",
            r"invalid (?:value .* for option )?preset",
            r"unable to parse option value.*preset",
        )
        if not (
            any(re.search(pattern, normalized) for pattern in backend_patterns[backend])
            or any(re.search(pattern, normalized) for pattern in generic_encoder_patterns)
        ):
            return None
        labels = {
            "nvenc": "NVIDIA NVENC 硬件编码器无法初始化",
            "qsv": "Intel Quick Sync 硬件编码器无法初始化",
            "amf": "AMD AMF 硬件编码器无法初始化",
            "videotoolbox": "Apple VideoToolbox 硬件编码器无法初始化",
            "vaapi": "Linux VAAPI 硬件编码器无法初始化",
        }
        return labels[backend]

    def _resolve_export_range(
        self,
        state: TimelineState,
        preset: ExportPreset,
        *,
        start_frame: int | None = None,
        end_frame: int | None = None,
    ) -> tuple[int, int]:
        del preset
        duration = max(1, state.duration_frames)
        if (start_frame is None) != (end_frame is None):
            raise ValueError("Explicit export range requires both start_frame and end_frame")
        if start_frame is not None and end_frame is not None:
            start = int(start_frame)
            end = int(end_frame)
            if start < 0 or end > duration:
                raise ValueError(
                    f"Explicit export range {start}:{end} exceeds timeline duration {duration}"
                )
        else:
            bounds = state.sequence.in_out
            start = min(duration, bounds.in_frame) if bounds else 0
            end = min(duration, bounds.out_frame) if bounds else duration
        if end <= start:
            raise ValueError("Sequence in and out points do not contain exportable media")
        return start, end

    def _external_subtitle_outputs(
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
            if not placements:
                continue
            lines: list[str] = []
            included = [
                placement
                for placement in placements
                if placement.end_frame > start_frame and placement.start_frame < end_frame
            ]
            if not included:
                continue
            destination = _subtitle_sidecar_destination(
                output,
                track.name,
            )
            destination_key = os.path.normcase(str(destination))
            if destination_key in used_destination_keys:
                destination = _subtitle_sidecar_destination(
                    output,
                    track.name,
                    collision_suffix=hashlib.sha256(
                        track.id.encode("utf-8")
                    ).hexdigest()[:12],
                )
                destination_key = os.path.normcase(str(destination))
            if destination_key in used_destination_keys:
                raise RuntimeError(
                    "Subtitle tracks resolve to the same sidecar path: "
                    f"{destination}"
                )
            used_destination_keys.add(destination_key)
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
    def _stage_external_subtitles(
        subtitle_outputs: tuple[ExternalSubtitleOutput, ...],
        output_set: OutputSetTransaction,
    ) -> tuple[Path, ...]:
        generated: list[Path] = []
        for subtitle in subtitle_outputs:
            temporary = output_set.temporary_path(
                subtitle.destination,
                "subtitle",
            )
            try:
                temporary.write_text(
                    subtitle.content,
                    encoding="utf-8-sig",
                )
                if (
                    temporary.read_text(encoding="utf-8-sig")
                    != subtitle.content
                ):
                    raise RuntimeError(
                        "External subtitle verification failed: "
                        f"{subtitle.destination}"
                    )
            except Exception:
                output_set.archive_staged(
                    subtitle.destination,
                )
                raise
            generated.append(subtitle.destination)
        return tuple(generated)

    def _consumer_properties(
        self,
        state: TimelineState,
        preset: _RuntimeExportPreset,
    ) -> list[str]:
        profile = state.sequence.profile
        values = [f"f={preset.container}"]
        codec = preset.video_codec or ""
        encoder_backend = codec_backend(codec)
        hardware_codec = encoder_backend not in {None, "software"}
        if preset.format == ExportFormat.H264:
            values.append(f"vcodec={codec or 'libx264'}")
        elif preset.format == ExportFormat.HEVC:
            values.append(f"vcodec={codec or 'libx265'}")
        elif preset.format == ExportFormat.AV1:
            values.append(f"vcodec={codec or 'libsvtav1'}")
        elif preset.format == ExportFormat.PRORES:
            values.extend(
                [
                    f"vcodec={preset.video_codec or 'prores_ks'}",
                    f"profile:v={preset.advanced.get('profile', 3)}",
                ]
            )
        elif preset.format == ExportFormat.AUDIO:
            values.append("vn=1")
        if preset.format in {ExportFormat.H264, ExportFormat.HEVC, ExportFormat.AV1}:
            if hardware_codec:
                if encoder_backend == "nvenc":
                    values.extend(["rc=vbr", f"cq={preset.quality_value:g}"])
                elif encoder_backend == "qsv":
                    values.append(f"global_quality={preset.quality_value:g}")
                elif encoder_backend == "amf":
                    values.append(f"qp_i={preset.quality_value:g}")
                elif encoder_backend == "vaapi":
                    values.extend(
                        [
                            f"qp={preset.quality_value:g}",
                            "vf=format=nv12,hwupload",
                        ]
                    )
                else:
                    values.append(f"q:v={preset.quality_value:g}")
            else:
                values.append(f"crf={preset.quality_value:g}")
            if encoder_backend in {None, "software", "nvenc", "qsv"}:
                values.append(f"preset={preset.preset}")
        if preset.pixel_format:
            values.append(f"pix_fmt={preset.pixel_format}")
        if preset.audio_codec:
            values.extend([f"acodec={preset.audio_codec}", f"ab={preset.audio_bitrate}"])
        else:
            values.append("an=1")
        values.append(f"g={preset.gop_frames}")
        if preset.advanced.get("profile") is not None and preset.format != ExportFormat.PRORES:
            values.append(f"profile={preset.advanced['profile']}")
        if preset.advanced.get("level"):
            values.append(f"level={preset.advanced['level']}")
        if preset.advanced.get("max_bitrate"):
            values.append(f"maxrate={int(preset.advanced['max_bitrate'])}")
        if preset.advanced.get("target_bitrate"):
            values.append(f"vb={int(preset.advanced['target_bitrate'])}")
        if preset.advanced.get("audio_sample_rate"):
            values.append(f"ar={int(preset.advanced['audio_sample_rate'])}")
        if preset.advanced.get("audio_channels"):
            values.append(f"ac={int(preset.advanced['audio_channels'])}")
        if preset.advanced.get("scaling_method"):
            values.append(f"sws_flags={preset.advanced['scaling_method']}")
        output_width = int(preset.advanced.get("width", profile.width))
        output_height = int(preset.advanced.get("height", profile.height))
        output_fps_numerator = int(preset.advanced.get("fps_numerator", profile.fps_numerator))
        output_fps_denominator = int(preset.advanced.get("fps_denominator", profile.fps_denominator))
        if (
            profile.color_mode == ColorMode.HDR10_BT2020_PQ
            or "width" in preset.advanced
            or "height" in preset.advanced
            or "fps_numerator" in preset.advanced
            or "fps_denominator" in preset.advanced
        ):
            values.extend(
                [
                    f"s={output_width}x{output_height}",
                    f"r={output_fps_numerator}/{output_fps_denominator}",
                ]
            )
        if profile.color_mode == ColorMode.HDR10_BT2020_PQ:
            if preset.format not in {ExportFormat.HEVC, ExportFormat.AV1, ExportFormat.PRORES}:
                raise ValueError("HDR10 export requires HEVC, AV1, or ProRes")
            values.extend(
                [
                    "color_primaries=bt2020",
                    "color_trc=smpte2084",
                    "colorspace=bt2020nc",
                ]
            )
            master_display = preset.advanced.get(
                "master_display",
                "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)",
            )
            max_cll = preset.advanced.get("max_cll", "1000,400")
            if preset.format == ExportFormat.HEVC and (preset.video_codec or "libx265") == "libx265":
                values.append(
                    "x265-params="
                    f"hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:"
                    f"colormatrix=bt2020nc:master-display={master_display}:max-cll={max_cll}"
                )
            elif preset.format == ExportFormat.AV1 and (preset.video_codec or "libsvtav1") == "libsvtav1":
                values.append("svtav1-params=enable-hdr=1")
        return values

    def probe(self, output: Path) -> dict:
        result = run_cancellable(
            [
                str(self.paths.ffprobe),
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-read_intervals",
                "%+#1",
                "-show_frames",
                "-of",
                "json",
                str(output),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=0,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Export verification failed: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def validate_probe(
        self,
        state: TimelineState,
        preset: ExportPreset,
        probe: dict,
        *,
        expected_duration_frames: int,
    ) -> None:
        runtime_preset = (
            preset
            if isinstance(preset, _RuntimeExportPreset)
            else self._resolve_runtime_preset(preset)
        )
        preset = runtime_preset
        streams = probe.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if preset.audio_codec and audio is None:
            raise RuntimeError("Export has no audio stream")
        if audio is not None:
            expected_audio_codecs = {
                "aac": {"aac"},
                "libopus": {"opus"},
                "opus": {"opus"},
                "flac": {"flac"},
                "pcm_s16le": {"pcm_s16le"},
                "pcm_s24le": {"pcm_s24le"},
            }
            expected = expected_audio_codecs.get(preset.audio_codec or "")
            if expected and audio.get("codec_name") not in expected:
                raise RuntimeError(
                    "Export audio codec does not match the preset: "
                    f"expected {sorted(expected)}, got {audio.get('codec_name')}"
                )
            expected_channels = preset.advanced.get("audio_channels")
            if expected_channels and int(audio.get("channels") or 0) != int(expected_channels):
                raise RuntimeError("Export audio channel count does not match the preset")
            expected_rate = preset.advanced.get("audio_sample_rate")
            if expected_rate and int(audio.get("sample_rate") or 0) != int(expected_rate):
                raise RuntimeError("Export audio sample rate does not match the preset")
        if preset.format == ExportFormat.AUDIO and audio is not None:
            if video is not None:
                raise RuntimeError("Audio-only export unexpectedly contains video")
        if preset.format != ExportFormat.AUDIO:
            if video is None:
                raise RuntimeError("Export has no video stream")
            expected_codec = {
                ExportFormat.H264: "h264",
                ExportFormat.HEVC: "hevc",
                ExportFormat.AV1: "av1",
                ExportFormat.PRORES: "prores",
            }[preset.format]
            if video.get("codec_name") != expected_codec:
                raise RuntimeError(
                    f"Export codec mismatch: expected {expected_codec}, got {video.get('codec_name')}"
                )
            profile = state.sequence.profile
            expected_width = int(preset.advanced.get("width", profile.width))
            expected_height = int(preset.advanced.get("height", profile.height))
            if (
                int(video.get("width") or 0) != expected_width
                or int(video.get("height") or 0) != expected_height
            ):
                raise RuntimeError(
                    "Export resolution does not match the sequence profile: "
                    f"expected {expected_width}x{expected_height}, got "
                    f"{video.get('width')}x{video.get('height')}"
                )
            if preset.pixel_format and video.get("pix_fmt") != preset.pixel_format:
                raise RuntimeError(
                    "Export pixel format does not match the preset: "
                    f"expected {preset.pixel_format}, got {video.get('pix_fmt')}"
                )
            expected_fps = Fraction(
                int(preset.advanced.get("fps_numerator", profile.fps_numerator)),
                int(preset.advanced.get("fps_denominator", profile.fps_denominator)),
            )
            actual_fps_text = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
            actual_fps = Fraction(actual_fps_text)
            if abs(actual_fps - expected_fps) > Fraction(1, 1000):
                raise RuntimeError(f"Export frame rate mismatch: expected {expected_fps}, got {actual_fps}")
            if profile.color_mode == ColorMode.HDR10_BT2020_PQ:
                pixel_format = str(video.get("pix_fmt") or "")
                if "10" not in pixel_format and "12" not in pixel_format:
                    raise RuntimeError("HDR10 export is not 10-bit")
                if video.get("color_primaries") != "bt2020" or video.get("color_transfer") != "smpte2084":
                    raise RuntimeError("HDR10 export metadata is incomplete")
                side_data = list(video.get("side_data_list") or [])
                frames = probe.get("frames") or []
                if frames:
                    side_data.extend(frames[0].get("side_data_list") or [])
                side_types = {item.get("side_data_type") for item in side_data}
                if preset.format == ExportFormat.HEVC and (preset.video_codec or "libx265") == "libx265":
                    required = {"Mastering display metadata", "Content light level metadata"}
                    if not required.issubset(side_types):
                        raise RuntimeError(
                            "HDR10 export is missing mastering-display or content-light metadata"
                        )
        profile = state.sequence.profile
        expected_fps = Fraction(
            int(preset.advanced.get("fps_numerator", profile.fps_numerator)),
            int(preset.advanced.get("fps_denominator", profile.fps_denominator)),
        )
        expected_duration = Fraction(
            expected_duration_frames,
            1,
        ) / Fraction(profile.fps_numerator, profile.fps_denominator)
        actual_duration = Fraction(str(probe.get("format", {}).get("duration") or "0"))
        duration_tolerance = Fraction(2, 1) / expected_fps
        if preset.format == ExportFormat.AUDIO:
            duration_tolerance = max(duration_tolerance, Fraction(1, 10))
        if abs(actual_duration - expected_duration) > duration_tolerance:
            raise RuntimeError(
                f"Export duration mismatch: expected {float(expected_duration):.3f}s, "
                f"got {float(actual_duration):.3f}s"
            )
