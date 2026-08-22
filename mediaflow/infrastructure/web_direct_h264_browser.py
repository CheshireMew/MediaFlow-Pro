from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.web_rendering import WebRenderPlan

from .web_browser import verify_non_monotonic_seek_pixels
from .web_capture_models import WebFrameCaptureError
from .web_capture_page import (
    _seek_frame,
    capture_chrome_screenshot,
    capture_fast_png,
)
from .web_capture_quality import _compare_fast_capture
from .web_capture_scripts import (
    FAST_CAPTURE_COMPATIBILITY,
    INJECT_FAST_CAPTURE_CANVAS,
)
from .web_direct_h264_attestation import EncoderTraceAttestation
from .web_direct_h264_codec import (
    ENCODE_CURRENT_CANVAS,
    FINISH_ENCODER,
    H264_CODEC,
    INITIALIZE_ENCODER,
    MAX_ENCODE_QUEUE_SIZE,
    MAX_PENDING_WRITES,
    BoundedChunkSink,
    encoder_bitrate,
    round_microseconds,
)
from .web_direct_h264_models import (
    BrowserEncodeResult,
    DirectH264FallbackRequired,
    EncoderTraceEvidence,
)
from .web_render_target import WebRenderTarget

TRACE_ATTESTATION_FRAMES = 8


def collect_gpu_evidence(browser) -> dict[str, object] | None:
    try:
        session = browser.new_browser_cdp_session()
        payload = session.send("SystemInfo.getInfo")
        gpu = payload.get("gpu") or {}
        devices = gpu.get("devices") or []
        normalized_devices = [
            {
                "vendor_id": device.get("vendorId"),
                "device_id": device.get("deviceId"),
                "vendor": device.get("vendorString"),
                "device": device.get("deviceString"),
            }
            for device in devices
            if isinstance(device, dict)
        ]
        first = devices[0] if devices else {}
        auxiliary = gpu.get("auxAttributes") or {}
        return {
            "vendor_id": first.get("vendorId"),
            "device_id": first.get("deviceId"),
            "vendor": first.get("vendorString"),
            "device": first.get("deviceString"),
            "driver_vendor": auxiliary.get("driverVendor"),
            "driver_version": auxiliary.get("driverVersion"),
            "gl_renderer": auxiliary.get("glRenderer"),
            "devices": normalized_devices,
        }
    except BaseException:
        return None


def open_capture_page(
    browser,
    *,
    capture_url: str,
    allowed_origin: str,
    runtime_state: dict[str, Any],
    width: int,
    height: int,
):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
    )
    page_errors: list[str] = []
    resource_failures: list[str] = []
    try:
        context.route(
            "http://**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(allowed_origin)
                else route.abort()
            ),
        )
        context.route("https://**/*", lambda route: route.abort())
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "requestfailed",
            lambda request: resource_failures.append(
                f"{request.url}: {request.failure or 'request failed'}"
            ),
        )
        page.goto(capture_url, wait_until="load", timeout=15_000)
        page.wait_for_function(
            """() => window.editableMedia
                && window.editableMedia.ready instanceof Promise
                && window.__hf
                && typeof window.__hf.seek === "function"
                && window.__hf.duration > 0""",
            timeout=15_000,
        )
        page.evaluate("() => window.editableMedia.ready")
        page.evaluate(
            """async () => {
                await document.fonts.ready;
                await Promise.all(Array.from(document.images).map(image => image.decode()));
            }"""
        )
        roundtrip = page.evaluate(
            """state => {
                window.editableMedia.setState(state);
                return window.editableMedia.getState();
            }""",
            runtime_state,
        )
        if roundtrip != runtime_state:
            raise DirectH264FallbackRequired(
                "editable-media runtime rejected the persisted clip state"
            )
        cdp = context.new_cdp_session(page)
        cdp.send(
            "Emulation.setDefaultBackgroundColorOverride",
            {"color": {"r": 0, "g": 0, "b": 0, "a": 0}},
        )
        return context, page, cdp, page_errors, resource_failures
    except BaseException:
        context.close()
        raise


def require_clean_page(page_errors: list[str], resource_failures: list[str]) -> None:
    if page_errors:
        raise DirectH264FallbackRequired(
            f"page runtime error blocked direct H.264: {page_errors[0]}"
        )
    if resource_failures:
        raise DirectH264FallbackRequired(
            f"resource load failure blocked direct H.264: {resource_failures[0]}"
        )


def verify_capture_surface(
    browser,
    *,
    capture_url: str,
    allowed_origin: str,
    runtime_state: dict[str, Any],
    target: WebRenderTarget,
    render_plan: WebRenderPlan,
) -> None:
    context, page, cdp, page_errors, resource_failures = open_capture_page(
        browser,
        capture_url=capture_url,
        allowed_origin=allowed_origin,
        runtime_state=runtime_state,
        width=target.width,
        height=target.height,
    )
    try:
        compatibility = page.evaluate(FAST_CAPTURE_COMPATIBILITY)
        if compatibility.get("supported") is not True:
            raise DirectH264FallbackRequired(
                "drawElementImage rejected direct H.264: "
                f"{compatibility.get('reason') or 'unknown'}"
            )
        duration = float(page.evaluate("() => window.__hf.duration"))
        verify_non_monotonic_seek_pixels(
            page,
            duration,
            lambda: capture_chrome_screenshot(cdp, target.width, target.height),
        )
        references: dict[int, bytes] = {}
        for verification in render_plan.verification_frames:
            _seek_frame(page, verification.time_seconds, verification.frame_index)
            references[verification.frame_index] = capture_chrome_screenshot(
                cdp,
                target.width,
                target.height,
            )
        page.evaluate(
            INJECT_FAST_CAPTURE_CANVAS,
            {"width": target.width, "height": target.height},
        )
        for verification in render_plan.verification_frames:
            _seek_frame(page, verification.time_seconds, verification.frame_index)
            candidate = capture_fast_png(page, target.width, target.height)
            comparison = _compare_fast_capture(
                references[verification.frame_index],
                candidate,
            )
            if not comparison.accepted:
                raise DirectH264FallbackRequired(
                    "direct H.264 verification frame differs from Chrome screenshot: "
                    f"frame={verification.frame_index}, {comparison.rejection_reason()}"
                )
        require_clean_page(page_errors, resource_failures)
    except WebFrameCaptureError as error:
        raise DirectH264FallbackRequired(
            f"direct H.264 verification seek failed: {error}"
        ) from error
    finally:
        context.close()


def encode_browser_stream(
    browser,
    *,
    capture_url: str,
    allowed_origin: str,
    runtime_state: dict[str, Any],
    target: WebRenderTarget,
    raw_path: Path,
    progress,
    check_cancelled,
) -> tuple[BrowserEncodeResult, BoundedChunkSink, float]:
    context, page, _cdp, page_errors, resource_failures = open_capture_page(
        browser,
        capture_url=capture_url,
        allowed_origin=allowed_origin,
        runtime_state=runtime_state,
        width=target.width,
        height=target.height,
    )
    encode_started = time.perf_counter()
    attestation = EncoderTraceAttestation(
        browser,
        frame_limit=min(TRACE_ATTESTATION_FRAMES, target.frame_count),
    )
    trace_evidence: EncoderTraceEvidence | None = None
    try:
        page.evaluate(
            INJECT_FAST_CAPTURE_CANVAS,
            {"width": target.width, "height": target.height},
        )
        requested_config: dict[str, object] = {
            "codec": H264_CODEC,
            "width": target.width,
            "height": target.height,
            "bitrate": encoder_bitrate(target),
            "framerate": target.fps_numerator / target.fps_denominator,
            "hardwareAcceleration": "prefer-hardware",
            "latencyMode": "realtime",
            "avc": {"format": "annexb"},
        }
        with raw_path.open("xb") as stream:
            sink = BoundedChunkSink(stream)
            page.expose_function("__mediaflowWriteEncodedChunk", sink.write)
            support = page.evaluate(
                INITIALIZE_ENCODER,
                {
                    "config": requested_config,
                    "maximumEncodeQueueSize": MAX_ENCODE_QUEUE_SIZE,
                    "maximumPendingWrites": MAX_PENDING_WRITES,
                },
            )
            if support.get("supported") is not True:
                raise DirectH264FallbackRequired(
                    str(support.get("reason") or "Chromium rejected direct H.264")
                )
            keyframe_interval = max(
                1,
                round(target.fps_numerator / target.fps_denominator),
            )
            attestation_start_frame = max(
                0,
                target.frame_count - TRACE_ATTESTATION_FRAMES,
            )
            for frame_index in range(target.frame_count):
                if check_cancelled is not None:
                    check_cancelled()
                if frame_index == attestation_start_frame:
                    attestation.start()
                seconds = frame_index * target.fps_denominator / target.fps_numerator
                timestamp = round_microseconds(
                    frame_index,
                    target.fps_numerator,
                    target.fps_denominator,
                )
                next_timestamp = round_microseconds(
                    frame_index + 1,
                    target.fps_numerator,
                    target.fps_denominator,
                )
                try:
                    _seek_frame(page, seconds, frame_index)
                    page.evaluate(
                        ENCODE_CURRENT_CANVAS,
                        {
                            "timestamp": timestamp,
                            "duration": next_timestamp - timestamp,
                            "keyFrame": frame_index % keyframe_interval == 0,
                            "width": target.width,
                            "height": target.height,
                        },
                    )
                except WebFrameCaptureError as error:
                    raise DirectH264FallbackRequired(
                        f"direct H.264 frame seek failed: {error}"
                    ) from error
                require_clean_page(page_errors, resource_failures)
                if progress:
                    progress(
                        OperationProgress.determinate(
                            "web_rendering",
                            completed=frame_index + 1,
                            total=target.frame_count,
                            unit="frames",
                        )
                    )
            finished = page.evaluate(FINISH_ENCODER)
            trace_evidence = attestation.finish(page)
            stream.flush()
        require_clean_page(page_errors, resource_failures)
        if trace_evidence is None:
            raise DirectH264FallbackRequired(
                "Chromium encoder attestation did not cover the direct render"
            )
        accepted_config = finished.get("config")
        if not isinstance(accepted_config, dict):
            raise DirectH264FallbackRequired("WebCodecs omitted its accepted encoder config")
        return (
            BrowserEncodeResult(
                requested_config=requested_config,
                accepted_config=accepted_config,
                chunk_count=int(finished.get("chunkCount") or 0),
                encoded_bytes=int(finished.get("encodedBytes") or 0),
                maximum_encode_queue_size=int(
                    finished.get("maximumObservedEncodeQueueSize") or 0
                ),
                maximum_pending_writes=int(
                    finished.get("maximumObservedPendingWrites") or 0
                ),
                attestation=trace_evidence,
            ),
            sink,
            time.perf_counter() - encode_started,
        )
    finally:
        attestation.abort()
        context.close()
