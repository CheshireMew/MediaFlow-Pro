from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.encoder_catalog import (
    is_hardware_codec,
    software_encoder_for_format,
)
from mediaflow.infrastructure.encoder_policy import VideoEncoderPolicyResolver
from mediaflow.infrastructure.font_assets import apply_bundled_font_environment
from mediaflow.infrastructure.output_reservation import (
    OutputSetTransaction,
    output_set_transaction,
)
from mediaflow.infrastructure.process_observers import MeltProgressObserver
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import (
    run_cancellable_streaming,
)

from .compiler import TimelineCompiler
from .export_encoder import MltExportEncoder, diagnostic_tail, hardware_failure_reason
from .export_plan import MltExportPlanner
from .export_probe import MltExportProbe
from .export_sidecars import MltSubtitleSidecars
from .export_types import (
    ExportResult,
    ExternalSubtitleOutput,
    MltExportRequest,
    RuntimeExportPreset,
)


class ExportAttemptError(RuntimeError):
    def __init__(
        self,
        diagnostic: str,
        *,
        archived_output: Path | None,
        returncode: int | None = None,
        archived_outputs: tuple[Path, ...] = (),
    ) -> None:
        super().__init__("MLT export failed:\n" + diagnostic)
        self.diagnostic = diagnostic
        self.returncode = returncode
        self.archived_outputs = archived_outputs + (
            (archived_output,) if archived_output is not None else ()
        )

    @property
    def archived_output(self) -> Path | None:
        return self.archived_outputs[-1] if self.archived_outputs else None

    @property
    def is_retryable_process_crash(self) -> bool:
        if os.name != "nt" or self.returncode is None:
            return False
        return self.returncode & 0xFFFFFFFF == 0xC0000005


class MltExportService:
    def __init__(
        self,
        compiler: TimelineCompiler,
        paths: RuntimePaths,
        encoder_resolver: VideoEncoderPolicyResolver | None = None,
    ):
        self.compiler = compiler
        self.paths = paths
        resolver = encoder_resolver or VideoEncoderPolicyResolver(self.paths)
        self.encoder = MltExportEncoder(self.paths, resolver)
        self.sidecars = MltSubtitleSidecars(self.compiler)
        self.planner = MltExportPlanner(self.paths, self.encoder, self.sidecars)
        self.probe_reader = MltExportProbe(self.paths)

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
        plans = self.planner.plans_for_requests(
            request_items,
            overwrite=overwrite,
        )
        with output_set_transaction(
            (destination for plan in plans for destination in plan.destinations),
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

        self.planner.plans_for_requests(
            tuple(requests),
            overwrite=overwrite,
        )

    def execution_identity(self, preset: ExportPreset) -> dict[str, object]:
        return self.encoder.execution_identity(preset)

    def _compile_graph(
        self,
        state: TimelineState,
        preset: RuntimeExportPreset,
        output: Path,
        *,
        progress=None,
    ) -> Path:
        graph_key = hashlib.sha256(os.path.normcase(str(output)).encode("utf-8")).hexdigest()[:12]
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
        preset: RuntimeExportPreset,
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
        requested_codec = preset.video_codec
        archived_outputs: list[Path] = []
        hardware_fallback_reason: str | None = None
        hardware_failure_details: str | None = None
        try:
            probe, recovered_outputs = self._render_with_process_recovery(
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
            archived_outputs.extend(recovered_outputs)
            actual_codec = requested_codec
        except ExportAttemptError as failure:
            reason = hardware_failure_reason(requested_codec, failure.diagnostic)
            fallback = software_encoder_for_format(preset.format)
            if (
                not is_hardware_codec(requested_codec)
                or preset.encoder_policy is None
                or preset.encoder_policy.mode != "prefer_hardware"
                or reason is None
                or fallback is None
            ):
                raise
            archived_outputs.extend(failure.archived_outputs)
            hardware_fallback_reason = reason
            hardware_failure_details = diagnostic_tail(failure.diagnostic)
            if progress:
                progress(OperationProgress.indeterminate("export_hardware_encoder_fallback"))
            fallback_preset = preset.model_copy(
                update={
                    "video_codec": fallback.codec,
                    "preset": fallback.preset,
                }
            )
            try:
                probe, recovered_outputs = self._render_with_process_recovery(
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
                archived_outputs.extend(recovered_outputs)
            except ExportAttemptError as fallback_failure:
                archived_outputs.extend(fallback_failure.archived_outputs)
                raise RuntimeError(
                    "Hardware encoding failed and the software recovery attempt also failed.\n"
                    f"Hardware attempt:\n{diagnostic_tail(failure.diagnostic)}\n"
                    f"Software attempt:\n{diagnostic_tail(fallback_failure.diagnostic)}"
                ) from fallback_failure
            actual_codec = fallback.codec
        subtitle_files = self.sidecars.stage(
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

    def _render_with_process_recovery(
        self,
        state: TimelineState,
        preset: RuntimeExportPreset,
        graph_path: Path,
        output: Path,
        output_set: OutputSetTransaction,
        *,
        start_frame: int,
        end_frame: int,
        attempt_label: str,
        progress=None,
        check_cancelled=None,
    ) -> tuple[dict, tuple[Path, ...]]:
        first_failure: ExportAttemptError | None = None
        try:
            return (
                self._render_attempt(
                    state,
                    preset,
                    graph_path,
                    output,
                    output_set,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    attempt_label=attempt_label,
                    progress=progress,
                    check_cancelled=check_cancelled,
                ),
                (),
            )
        except ExportAttemptError as error:
            if (
                not error.is_retryable_process_crash
                or is_hardware_codec(preset.video_codec)
            ):
                raise
            first_failure = error
        if first_failure is None:
            raise AssertionError("MLT crash recovery entered without a failed attempt")
        if check_cancelled:
            check_cancelled()
        if progress:
            progress(OperationProgress.indeterminate("export_rendering"))
        try:
            probe = self._render_attempt(
                state,
                preset,
                graph_path,
                output,
                output_set,
                start_frame=start_frame,
                end_frame=end_frame,
                attempt_label=f"{attempt_label}-retry",
                progress=progress,
                check_cancelled=check_cancelled,
            )
        except ExportAttemptError as retry_failure:
            combined = (
                "MLT process crash recovery failed.\n"
                f"First attempt:\n{first_failure.diagnostic}\n"
                f"Recovery attempt:\n{retry_failure.diagnostic}"
            )
            raise ExportAttemptError(
                combined,
                archived_output=None,
                returncode=retry_failure.returncode,
                archived_outputs=(
                    first_failure.archived_outputs
                    + retry_failure.archived_outputs
                ),
            ) from retry_failure
        return probe, first_failure.archived_outputs

    def _render_attempt(
        self,
        state: TimelineState,
        preset: RuntimeExportPreset,
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
        consumer = self.encoder.consumer_properties(state, preset)
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
        if result.returncode != 0 or not attempt_output.is_file() or attempt_output.stat().st_size == 0:
            process_output = "\n".join(
                part for part in (result.stdout, result.stderr) if part
            ).strip()
            diagnostic = f"melt exited with code {result.returncode}"
            if process_output:
                diagnostic += f"\n{process_output}"
            elif not attempt_output.is_file() or attempt_output.stat().st_size == 0:
                diagnostic += "; the export attempt did not produce a non-empty file"
            archived = output_set.archive_staged(output)
            raise ExportAttemptError(
                diagnostic,
                archived_output=archived,
                returncode=result.returncode,
            )
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
            probe = self.probe_reader.read(attempt_output)
            self.probe_reader.validate(
                state,
                preset,
                probe,
                expected_duration_frames=end_frame - start_frame,
            )
        except Exception:
            output_set.archive_staged(output)
            raise
        return probe

    def probe(self, output: Path) -> dict:
        return self.probe_reader.read(output)

    def validate_probe(
        self,
        state: TimelineState,
        preset: ExportPreset,
        probe: dict,
        *,
        expected_duration_frames: int,
    ) -> None:
        runtime_preset = (
            preset if isinstance(preset, RuntimeExportPreset) else self.encoder.resolve_preset(preset)
        )
        self.probe_reader.validate(
            state,
            runtime_preset,
            probe,
            expected_duration_frames=expected_duration_frames,
        )
