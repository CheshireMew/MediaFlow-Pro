from __future__ import annotations

from fractions import Fraction
from functools import partial as bind_arguments
from pathlib import Path

from mediaflow.application.task_execution_types import TaskLeaseLost, TaskStopped
from mediaflow.application.web_package_files import web_package_root
from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_manifest import WebAssetSpec
from mediaflow.domain.web_rendering import WebRenderActualCapture, WebRenderPlan
from mediaflow.domain.web_state import (
    WebClipState,
    web_runtime_state,
)

from .ffmpeg_runner import FfmpegRunner
from .ffprobe_runner import FfprobeRunner
from .runtime_paths import RuntimePaths
from .web_browser import WebPackagePreviewServer
from .web_capture_engine import (
    FastCaptureFallbackRequired,
    WebCaptureMetrics,
    WebCaptureMode,
    get_web_capture_engine,
    release_web_capture_engine,
)
from .web_direct_h264 import (
    DirectH264FallbackRequired,
    render_webcodecs_h264,
)
from .web_render_ffmpeg import build_web_render_ffmpeg_command
from .web_render_manifest import publish_web_render_manifest
from .web_render_probe import WebRenderProbe
from .web_render_target import WebRenderTarget
from .web_segment_assembler import WebSegmentAssembler


class WebBrowserCacheRenderer:
    def __init__(self, paths: RuntimePaths, ffmpeg: FfmpegRunner):
        self.paths = paths
        self.ffmpeg = ffmpeg
        self.ffprobe = FfprobeRunner(self.paths.ffprobe)
        self.probe = WebRenderProbe(self.ffmpeg, self.ffprobe)
        self.segment_assembler = WebSegmentAssembler(self.ffmpeg, self.probe)

    def render(
        self,
        entry: Path,
        spec: WebAssetSpec,
        clip_state: WebClipState,
        state: TimelineState,
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
        *,
        progress=None,
        check_cancelled=None,
        capture_start_frame: int = 0,
    ) -> None:
        executable = self.paths.chromium
        if executable is None or not executable.is_file():
            raise FileNotFoundError("Pinned Playwright Chromium is unavailable")
        engine = None
        manifest = spec.manifest
        package_root = web_package_root(entry, manifest)
        variant = manifest.variant_for(clip_state.variant.id if clip_state.variant is not None else None)
        runtime_state = web_runtime_state(clip_state, manifest)
        partial = unique_temporary_sibling(target.path, label="web-render")
        try:
            with WebPackagePreviewServer(package_root) as preview:
                capture_url = preview.url_for(
                    manifest.entry,
                    query=(f"capture=1&variant={variant.id}&scene={runtime_state['scene_id']}"),
                )
                capture_modes: tuple[WebCaptureMode, ...] = (
                    ("screenshot",)
                    if render_plan.capture_mode == "screenshot"
                    else ("auto", "screenshot")
                )
                direct_failure: str | None = None
                probe: dict[str, object] | None = None
                actual_capture: WebRenderActualCapture | None = None
                if target.animated and render_plan.planned_backend == "webcodecs-h264":
                    try:
                        actual_capture = render_webcodecs_h264(
                            executable=executable,
                            capture_url=capture_url,
                            allowed_origin=preview.url_for(""),
                            runtime_state=runtime_state,
                            target=target,
                            render_plan=render_plan,
                            output_path=partial,
                            ffmpeg=self.ffmpeg,
                            progress=progress,
                            check_cancelled=check_cancelled,
                        )
                        probe = self.probe.validate(partial, target, actual_capture)
                    except DirectH264FallbackRequired as error:
                        direct_failure = str(error)
                        actual_capture = None
                        probe = None
                        partial.unlink(missing_ok=True)
                    except RuntimeError as error:
                        if isinstance(error, (TaskStopped, TaskLeaseLost)):
                            raise
                        direct_failure = f"direct H.264 output validation failed: {error}"
                        actual_capture = None
                        probe = None
                        partial.unlink(missing_ok=True)
                if target.animated and actual_capture is None:
                    engine = get_web_capture_engine(executable)
                    capture_metrics = self._render_animation(
                        engine,
                        preview,
                        capture_url,
                        variant.canvas.width,
                        variant.canvas.height,
                        spec,
                        runtime_state,
                        state,
                        target,
                        partial,
                        capture_modes=capture_modes,
                        progress=progress,
                        check_cancelled=check_cancelled,
                        capture_start_frame=capture_start_frame,
                        initial_fallback_reason=direct_failure,
                    )
                    actual_capture = self._actual_capture(capture_metrics)
                else:
                    if actual_capture is not None:
                        capture_metrics = None
                    else:
                        engine = get_web_capture_engine(executable)
                        capture_metrics = self._render_still(
                            engine,
                            preview,
                            capture_url,
                            variant.canvas.width,
                            variant.canvas.height,
                            spec,
                            runtime_state,
                            state,
                            target,
                            partial,
                            capture_modes=capture_modes,
                            progress=progress,
                            check_cancelled=check_cancelled,
                            capture_start_frame=capture_start_frame,
                        )
                        actual_capture = self._actual_capture(capture_metrics)
            if actual_capture is None:
                raise RuntimeError("Editable web renderer returned no capture evidence")
            if probe is None:
                probe = self.probe.validate(partial, target, actual_capture)
            partial.replace(target.path)
            publish_web_render_manifest(target, probe, render_plan, actual_capture)
        finally:
            partial.unlink(missing_ok=True)
            if engine is not None:
                release_web_capture_engine(executable, engine)

    def compose_segments(
        self,
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
        segments: list[tuple[WebRenderTarget, WebRenderPlan, bool]],
        *,
        check_cancelled=None,
    ) -> None:
        self.segment_assembler.compose(
            target,
            render_plan,
            segments,
            check_cancelled=check_cancelled,
        )

    def _render_animation(
        self,
        engine,
        preview: WebPackagePreviewServer,
        capture_url: str,
        width: int,
        height: int,
        spec: WebAssetSpec,
        runtime_state: dict,
        state: TimelineState,
        target: WebRenderTarget,
        partial: Path,
        *,
        capture_modes: tuple[WebCaptureMode, ...],
        progress,
        check_cancelled,
        capture_start_frame: int,
        initial_fallback_reason: str | None = None,
    ) -> WebCaptureMetrics:
        fps = Fraction(
            state.sequence.profile.fps_numerator,
            state.sequence.profile.fps_denominator,
        )
        command = build_web_render_ffmpeg_command(target, partial)
        if progress:
            progress(
                OperationProgress.determinate(
                    "web_rendering", completed=0, total=target.frame_count, unit="frames"
                )
            )

        def report_frame(completed: int) -> None:
            if progress:
                progress(
                    OperationProgress.determinate(
                        "web_rendering",
                        completed=completed,
                        total=target.frame_count,
                        unit="frames",
                    )
                )

        fallback_reason = initial_fallback_reason
        for capture_mode in capture_modes:
            pipe = self.ffmpeg.open_input_pipe(command)
            try:
                metrics = engine.render_frames(
                    url=capture_url,
                    allowed_origin=preview.url_for(""),
                    width=width,
                    height=height,
                    fps_numerator=fps.numerator,
                    fps_denominator=fps.denominator,
                    runtime_state=runtime_state,
                    determinism_key=target.key,
                    frame_count=target.frame_count,
                    on_frame=bind_arguments(pipe.write, check_cancelled=check_cancelled),
                    on_progress=report_frame,
                    check_cancelled=check_cancelled,
                    capture_mode=capture_mode,
                    fallback_reason=fallback_reason,
                    retry_limit=spec.manifest.frame_readiness.retry_limit,
                    start_frame=capture_start_frame,
                )
            except FastCaptureFallbackRequired as error:
                pipe.abort()
                fallback_reason = "; ".join(
                    item for item in (fallback_reason, str(error)) if item
                )
                continue
            except BaseException:
                pipe.abort()
                raise
            result = pipe.finish(timeout=1800)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg editable web media render failed: {result.stderr}")
            return metrics
        raise RuntimeError("Editable web media capture exhausted its screenshot fallback")

    @staticmethod
    def _render_still(
        engine,
        preview: WebPackagePreviewServer,
        capture_url: str,
        width: int,
        height: int,
        spec: WebAssetSpec,
        runtime_state: dict,
        state: TimelineState,
        target: WebRenderTarget,
        partial: Path,
        *,
        capture_modes: tuple[WebCaptureMode, ...],
        progress,
        check_cancelled,
        capture_start_frame: int,
    ) -> WebCaptureMetrics:
        if progress:
            progress(OperationProgress.determinate("web_rendering", completed=0, total=1, unit="frames"))
        frames: list[bytes] = []
        fallback_reason: str | None = None
        for capture_mode in capture_modes:
            frames.clear()
            try:
                metrics = engine.render_frames(
                    url=capture_url,
                    allowed_origin=preview.url_for(""),
                    width=width,
                    height=height,
                    fps_numerator=state.sequence.profile.fps_numerator,
                    fps_denominator=state.sequence.profile.fps_denominator,
                    runtime_state=runtime_state,
                    determinism_key=target.key,
                    frame_count=1,
                    on_frame=frames.append,
                    check_cancelled=check_cancelled,
                    capture_mode=capture_mode,
                    fallback_reason=fallback_reason,
                    retry_limit=spec.manifest.frame_readiness.retry_limit,
                    start_frame=capture_start_frame,
                )
            except FastCaptureFallbackRequired as error:
                fallback_reason = str(error)
                continue
            break
        else:
            raise RuntimeError("Editable web media capture exhausted its screenshot fallback")
        partial.write_bytes(frames[0])
        if progress:
            progress(OperationProgress.determinate("web_rendering", completed=1, total=1, unit="frames"))
        return metrics

    def _probe(
        self,
        path: Path,
        target: WebRenderTarget,
        actual_capture: WebRenderActualCapture,
    ) -> dict[str, object]:
        return self.probe.validate(path, target, actual_capture)

    def _decode_representative_frames(
        self,
        path: Path,
        target: WebRenderTarget,
    ) -> None:
        self.probe._decode_representative_frames(path, target)

    def _probe_packet_clock(
        self,
        path: Path,
        target: WebRenderTarget,
        *,
        video_stream_index: int,
        audio_stream_index: int | None,
    ) -> dict[str, object]:
        return self.probe._probe_packet_clock(
            path,
            target,
            video_stream_index=video_stream_index,
            audio_stream_index=audio_stream_index,
        )

    @staticmethod
    def _actual_capture(metrics: WebCaptureMetrics) -> WebRenderActualCapture:
        return WebRenderActualCapture(
            backend=metrics.capture_backend,
            reason=metrics.capture_backend_reason,
            fallback_reason=metrics.fallback_reason,
            worker_count=metrics.worker_count,
            captured_frames=metrics.captured_frames,
            elapsed_seconds=metrics.elapsed_seconds,
        )

    @staticmethod
    def _publish_manifest(
        target: WebRenderTarget,
        probe: dict[str, object],
        render_plan: WebRenderPlan,
        actual_capture: WebRenderActualCapture,
        *,
        segmentation: dict[str, object] | None = None,
    ) -> None:
        publish_web_render_manifest(
            target,
            probe,
            render_plan,
            actual_capture,
            segmentation=segmentation,
        )
