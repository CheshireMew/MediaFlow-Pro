from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.web_rendering import (
    WebRenderActualCapture,
    WebRenderEncoderTelemetry,
    WebRenderPlan,
)

from .ffmpeg_runner import FfmpegRunner
from .web_direct_h264_browser import (
    collect_gpu_evidence,
    encode_browser_stream,
    verify_capture_surface,
)
from .web_direct_h264_codec import (
    H264_CODEC,
    MAX_ENCODE_QUEUE_SIZE,
    MAX_PENDING_WRITES,
    round_microseconds,
    validate_encoded_chunks,
)
from .web_direct_h264_models import DirectH264FallbackRequired
from .web_render_ffmpeg import build_web_direct_h264_mux_command
from .web_render_target import WebRenderTarget

# Kept as a module-level test seam because frame-clock rounding is part of the
# public render evidence, even though the implementation belongs to the codec layer.
_round_microseconds = round_microseconds


def _require_nvidia_gpu_headroom() -> dict[str, object] | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        first = next(line.strip() for line in result.stdout.splitlines() if line.strip())
        utilization_text, used_text, total_text = (
            item.strip() for item in first.split(",", 2)
        )
        utilization = int(utilization_text)
        used = int(used_text)
        total = int(total_text)
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        return None
    memory_percent = (used * 100 / total) if total > 0 else 0
    if utilization >= 90 or memory_percent >= 90:
        raise DirectH264FallbackRequired(
            "NVIDIA GPU headroom is insufficient for direct H.264: "
            f"utilization={utilization}%, memory={memory_percent:.1f}%"
        )
    return {
        "provider": "nvidia-smi",
        "utilization_percent": utilization,
        "memory_used_mib": used,
        "memory_total_mib": total,
        "memory_percent": round(memory_percent, 1),
        "threshold_percent": 90,
    }


def render_webcodecs_h264(
    *,
    executable: Path,
    capture_url: str,
    allowed_origin: str,
    runtime_state: dict[str, Any],
    target: WebRenderTarget,
    render_plan: WebRenderPlan,
    output_path: Path,
    ffmpeg: FfmpegRunner,
    progress=None,
    check_cancelled=None,
) -> WebRenderActualCapture:
    if render_plan.planned_backend != "webcodecs-h264":
        raise ValueError("Direct H.264 renderer received a frame-pipe plan")
    if target.native_media_plan.video_segments:
        raise ValueError("Direct H.264 cannot consume native-underlay video")
    gpu_headroom = _require_nvidia_gpu_headroom()
    if progress:
        progress(
            OperationProgress.determinate(
                "web_rendering",
                completed=0,
                total=target.frame_count,
                unit="frames",
            )
        )
    raw_path = unique_temporary_sibling(
        output_path.with_suffix(".h264"),
        label="web-h264",
    )
    started = time.perf_counter()
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise DirectH264FallbackRequired(
            "Playwright is unavailable for direct H.264 rendering"
        ) from error
    try:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=str(executable),
                    headless=True,
                    args=[
                        "--disable-renderer-backgrounding",
                        "--disable-background-timer-throttling",
                        "--disable-backgrounding-occluded-windows",
                        "--enable-features=CanvasDrawElement",
                        "--force-color-profile=srgb",
                        "--hide-scrollbars",
                        "--mute-audio",
                    ],
                )
                try:
                    browser_version = browser.version
                    gpu = collect_gpu_evidence(browser)
                    if gpu_headroom is not None:
                        gpu = dict(gpu or {})
                        gpu["admission"] = gpu_headroom
                    verify_capture_surface(
                        browser,
                        capture_url=capture_url,
                        allowed_origin=allowed_origin,
                        runtime_state=runtime_state,
                        target=target,
                        render_plan=render_plan,
                    )
                    encoded, sink, encode_seconds = encode_browser_stream(
                        browser,
                        capture_url=capture_url,
                        allowed_origin=allowed_origin,
                        runtime_state=runtime_state,
                        target=target,
                        raw_path=raw_path,
                        progress=progress,
                        check_cancelled=check_cancelled,
                    )
                finally:
                    browser.close()
        except PlaywrightError as error:
            raise DirectH264FallbackRequired(
                f"Chromium direct H.264 attempt failed: {error}"
            ) from error
        validate_encoded_chunks(sink, target)
        if encoded.maximum_encode_queue_size > MAX_ENCODE_QUEUE_SIZE:
            raise DirectH264FallbackRequired(
                "WebCodecs exceeded the bounded encode queue: "
                f"limit={MAX_ENCODE_QUEUE_SIZE}, "
                f"observed={encoded.maximum_encode_queue_size}"
            )
        if encoded.maximum_pending_writes > MAX_PENDING_WRITES:
            raise DirectH264FallbackRequired(
                "WebCodecs exceeded the bounded encoded-chunk write queue: "
                f"limit={MAX_PENDING_WRITES}, "
                f"observed={encoded.maximum_pending_writes}"
            )
        if sink.bytes_written != encoded.encoded_bytes or not raw_path.is_file():
            raise DirectH264FallbackRequired(
                "WebCodecs encoded byte accounting does not match the streamed Annex-B file"
            )
        mux_started = time.perf_counter()
        mux_result = ffmpeg.run(
            build_web_direct_h264_mux_command(target, raw_path, output_path),
            check_cancelled=check_cancelled,
            timeout=1800,
        )
        mux_seconds = time.perf_counter() - mux_started
        if mux_result.returncode != 0:
            raise DirectH264FallbackRequired(
                f"FFmpeg rejected direct H.264 muxing: {mux_result.stderr.strip()}"
            )
        telemetry = WebRenderEncoderTelemetry(
            codec=H264_CODEC,
            browser_version=browser_version,
            requested_hardware_acceleration="prefer-hardware",
            hardware_acceleration_verified=(
                encoded.attestation.hardware_acceleration_verified
            ),
            zero_copy_verified=encoded.attestation.zero_copy_verified,
            attestation_method="chromium-trace",
            actual_encoder_name=encoded.attestation.actual_encoder_name,
            actual_encoder_type=encoded.attestation.actual_encoder_type,
            encoder_storage_type=encoded.attestation.encoder_storage_type,
            input_copy_path=encoded.attestation.input_copy_path,
            attested_frames=encoded.attestation.attested_frames,
            trace_event_count=encoded.attestation.trace_event_count,
            platform_encode_events=encoded.attestation.platform_encode_events,
            platform_output_events=encoded.attestation.platform_output_events,
            gpu_readback_events=encoded.attestation.gpu_readback_events,
            input_surface="canvas-videoframe",
            requested_config=encoded.requested_config,
            accepted_config=encoded.accepted_config,
            gpu=gpu,
            encoded_chunks=encoded.chunk_count,
            encoded_bytes=encoded.encoded_bytes,
            maximum_encode_queue_size=encoded.maximum_encode_queue_size,
            maximum_pending_writes=encoded.maximum_pending_writes,
            timestamps_monotonic=True,
            exact_frame_time_boundaries=True,
            encode_seconds=encode_seconds,
            mux_seconds=mux_seconds,
        )
        return WebRenderActualCapture(
            backend="webcodecs-h264",
            reason=(
                "browser WebCodecs streamed bounded Annex-B H.264 after deterministic seek "
                "and Chrome screenshot verification; Chromium trace proved "
                f"{encoded.attestation.actual_encoder_name} "
                "and recorded the actual input-copy path"
            ),
            worker_count=1,
            captured_frames=target.frame_count,
            elapsed_seconds=time.perf_counter() - started,
            encoder=telemetry,
        )
    finally:
        raw_path.unlink(missing_ok=True)
