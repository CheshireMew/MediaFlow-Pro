from __future__ import annotations

import atexit
import base64
import math
import os
import queue
import struct
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mediaflow.infrastructure.system_resources import available_physical_memory_bytes
from mediaflow.infrastructure.web_browser import (
    SEEK_WEB_FRAME_SCRIPT,
    verify_non_monotonic_seek_pixels,
)

_BROWSER_IDLE_SECONDS = 30.0
_FRAME_QUEUE_DEPTH = 2
_FAST_CAPTURE_MIN_PSNR_DB = 36.0
_FAST_CAPTURE_MIN_BLURRED_PSNR_DB = 43.0
_FAST_CAPTURE_MAX_MEAN_ABSOLUTE_ERROR = 0.75
_FAST_CAPTURE_MAX_BLURRED_CHANNEL_ERROR = 48
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

WebCaptureMode = Literal["auto", "screenshot"]
WebWorkerSizingBound = Literal["worker_limit", "work", "memory", "pixels"]

_FAST_CAPTURE_COMPATIBILITY = """
() => {
    const root = document.querySelector("[data-composition-id]");
    if (!root) return {supported: false, reason: "missing_root"};
    const probe = document.createElement("canvas").getContext("2d");
    if (!probe || typeof probe.drawElementImage !== "function") {
        return {supported: false, reason: "api_unavailable"};
    }
    const bounds = root.getBoundingClientRect();
    if (
        Math.abs(bounds.left) > 0.5
        || Math.abs(bounds.top) > 0.5
        || Math.abs(bounds.width - window.innerWidth) > 0.5
        || Math.abs(bounds.height - window.innerHeight) > 0.5
    ) {
        return {supported: false, reason: "root_not_viewport"};
    }
    if (root.querySelector("canvas, video, iframe, object, embed")) {
        return {supported: false, reason: "dynamic_surface"};
    }
    const animations = document.getAnimations().filter(
        animation => animation.playState === "running"
    );
    if (animations.length) return {supported: false, reason: "wall_clock_animation"};
    for (const element of [root, ...root.querySelectorAll("*")]) {
        const style = getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden") continue;
        if (
            (style.backdropFilter && style.backdropFilter !== "none")
            || (style.webkitBackdropFilter && style.webkitBackdropFilter !== "none")
            || (style.filter && style.filter !== "none")
            || (style.mixBlendMode && style.mixBlendMode !== "normal")
        ) {
            return {supported: false, reason: "unsupported_effect"};
        }
    }
    return {supported: true, reason: "eligible"};
}
"""

_INJECT_FAST_CAPTURE_CANVAS = """
({width, height}) => {
    const root = document.querySelector("[data-composition-id]");
    if (!root || document.getElementById("__mediaflow_capture_canvas")) return;
    const parent = root.parentNode;
    if (!parent) throw new Error("Editable media root has no parent");
    const canvas = document.createElement("canvas");
    canvas.id = "__mediaflow_capture_canvas";
    canvas.setAttribute("layoutsubtree", "");
    canvas.width = width;
    canvas.height = height;
    canvas.style.cssText = "display:block;position:absolute;top:0;left:0;z-index:0";
    parent.insertBefore(canvas, root);
    canvas.appendChild(root);
    const tick = document.createElement("div");
    tick.id = "__mediaflow_capture_tick";
    tick.style.cssText = [
        "position:absolute",
        "left:0",
        "top:0",
        "width:1px",
        "height:1px",
        "background:#000",
        "opacity:0.01",
        "pointer-events:none",
    ].join(";");
    canvas.appendChild(tick);
    window.__mediaflowInvalidateCapture = () => {
        tick.style.backgroundColor = tick.style.backgroundColor === "rgb(0, 0, 0)"
            ? "rgb(1, 1, 1)"
            : "rgb(0, 0, 0)";
        if (typeof canvas.requestPaint === "function") {
            try {
                canvas.requestPaint();
            } catch {
                // The paint sentinel remains a valid fallback.
            }
        }
    };
}
"""

_REMOVE_FAST_CAPTURE_CANVAS = """
() => {
    const canvas = document.getElementById("__mediaflow_capture_canvas");
    const root = document.querySelector("[data-composition-id]");
    if (canvas && root && canvas.parentNode) {
        canvas.parentNode.insertBefore(root, canvas);
        canvas.remove();
    }
    delete window.__mediaflowInvalidateCapture;
}
"""

_CAPTURE_FAST_PNG = """
({width, height}) => {
    const canvas = document.getElementById("__mediaflow_capture_canvas");
    const root = document.querySelector("[data-composition-id]");
    const context = canvas?.getContext("2d");
    if (!canvas || !root || !context || typeof context.drawElementImage !== "function") {
        throw new Error("drawElementImage capture is not initialized");
    }
    return new Promise((resolve, reject) => {
        let settled = false;
        const draw = () => {
            if (settled) return;
            settled = true;
            try {
                context.clearRect(0, 0, width, height);
                let background = "";
                for (let element = root.parentElement; element; element = element.parentElement) {
                    if (element === canvas) continue;
                    const color = getComputedStyle(element).backgroundColor;
                    if (color && color !== "transparent" && color !== "rgba(0, 0, 0, 0)") {
                        background = color;
                        break;
                    }
                }
                if (background) {
                    context.fillStyle = background;
                    context.fillRect(0, 0, width, height);
                }
                context.drawElementImage(root, 0, 0);
                setTimeout(() => {
                    try {
                        resolve(canvas.toDataURL("image/png"));
                    } catch (error) {
                        reject(error);
                    }
                }, 0);
            } catch (error) {
                reject(error);
            }
        };
        const onPaint = () => {
            canvas.removeEventListener("paint", onPaint);
            draw();
        };
        canvas.addEventListener("paint", onPaint);
        window.__mediaflowInvalidateCapture?.();
        setTimeout(() => {
            canvas.removeEventListener("paint", onPaint);
            draw();
        }, 250);
    });
}
"""


@dataclass(frozen=True, slots=True)
class WebCaptureWorkerSizing:
    workers: int
    bound_by: WebWorkerSizingBound
    worker_limit: int
    work_limit: int
    memory_limit: int
    pixel_limit: int
    available_memory_bytes: int
    estimated_worker_bytes: int


@dataclass(frozen=True, slots=True)
class WebCaptureMetrics:
    worker_count: int
    frame_count: int
    captured_frames: int
    fast_capture_workers: int
    capture_backend: Literal["drawelement", "screenshot"]
    capture_backend_reason: str
    fallback_reason: str | None
    sizing: WebCaptureWorkerSizing
    seek_seconds: float
    capture_seconds: float
    queue_wait_seconds: float
    frame_time_p50_ms: float
    frame_time_p95_ms: float
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class WebCaptureFailure:
    capture_mode: WebCaptureMode
    error_type: str
    message: str
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class WebCaptureDiagnostics:
    browser_launches: int
    render_count: int
    failed_render_count: int
    last_metrics: WebCaptureMetrics | None
    last_failure: WebCaptureFailure | None


@dataclass(frozen=True, slots=True)
class _CapturedFrame:
    index: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class _WorkerMetrics:
    captured_frames: int
    fast_capture: bool
    capture_backend_reason: str
    seek_seconds: float
    capture_seconds: float
    queue_wait_seconds: float
    frame_times_ms: tuple[float, ...]


class FastCaptureFallbackRequired(RuntimeError):
    """The current encoded attempt must be discarded and repeated with screenshots."""

    def __init__(self, *, worker_index: int, frame_index: int, reason: str) -> None:
        super().__init__(
            "drawElementImage capture failed after production started; "
            f"worker={worker_index}, frame={frame_index}, reason={reason}"
        )
        self.worker_index = worker_index
        self.frame_index = frame_index
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _FastCapturePlan:
    references: dict[int, bytes]


@dataclass(frozen=True, slots=True)
class _FastCaptureAttempt:
    plan: _FastCapturePlan | None
    reason: str


@dataclass(frozen=True, slots=True)
class _FastCaptureComparison:
    psnr_db: float
    blurred_psnr_db: float
    mean_absolute_error: float
    blurred_channel_error: int
    alpha_equal: bool

    @property
    def accepted(self) -> bool:
        return (
            self.psnr_db >= _FAST_CAPTURE_MIN_PSNR_DB
            and self.blurred_psnr_db >= _FAST_CAPTURE_MIN_BLURRED_PSNR_DB
            and self.mean_absolute_error
            <= _FAST_CAPTURE_MAX_MEAN_ABSOLUTE_ERROR
            and self.blurred_channel_error
            <= _FAST_CAPTURE_MAX_BLURRED_CHANNEL_ERROR
            and self.alpha_equal
        )

    def rejection_reason(self) -> str:
        return (
            "drawElementImage visual comparison failed: "
            f"psnr={self.psnr_db:.3f}dB, "
            f"blurred_psnr={self.blurred_psnr_db:.3f}dB, "
            f"mean_error={self.mean_absolute_error:.6f}, "
            f"blurred_channel_error={self.blurred_channel_error}, "
            f"alpha_equal={self.alpha_equal}"
        )


@dataclass(slots=True)
class _BooleanDecision:
    ready: threading.Event
    enabled: bool | None = None

    def publish(self, enabled: bool) -> None:
        self.enabled = enabled
        self.ready.set()

    def wait(self, cancelled: threading.Event) -> bool:
        while not self.ready.wait(timeout=0.1):
            if cancelled.is_set():
                return False
        return self.enabled is True


@dataclass(slots=True)
class _CaptureModeConsensus:
    worker_count: int
    ready: threading.Event
    lock: threading.Lock
    proposals: int = 0
    all_enabled: bool = True

    def propose(self, enabled: bool) -> None:
        with self.lock:
            self.proposals += 1
            self.all_enabled = self.all_enabled and enabled
            if self.proposals == self.worker_count:
                self.ready.set()

    def wait(self, cancelled: threading.Event) -> bool:
        while not self.ready.wait(timeout=0.1):
            if cancelled.is_set():
                return False
        return self.all_enabled


@dataclass(slots=True)
class _CaptureJob:
    url: str
    allowed_origin: str
    width: int
    height: int
    fps_numerator: int
    fps_denominator: int
    runtime_state: dict[str, Any]
    frame_indices: range
    output: queue.Queue[_CapturedFrame]
    cancelled: threading.Event
    future: Future[_WorkerMetrics]
    determinism_decision: _BooleanDecision
    verifies_determinism: bool
    capture_mode_consensus: _CaptureModeConsensus
    fast_capture_sample_indices: tuple[int, ...]
    capture_mode: WebCaptureMode


class _BrowserWorker:
    def __init__(
        self,
        *,
        executable: Path,
        index: int,
        on_browser_launch: Callable[[], None],
    ) -> None:
        self.executable = executable
        self.index = index
        self.on_browser_launch = on_browser_launch
        self.jobs: queue.Queue[_CaptureJob | None] = queue.Queue()
        self._state_lock = threading.Lock()
        self._active_job: _CaptureJob | None = None
        self.thread = threading.Thread(
            target=self._run,
            name=f"mediaflow-web-capture-{index}",
            daemon=True,
        )
        self.thread.start()

    def submit(self, job: _CaptureJob) -> None:
        self.jobs.put(job)

    def stop(self) -> None:
        with self._state_lock:
            if self._active_job is not None:
                self._active_job.cancelled.set()
        self.jobs.put(None)

    def join(self, timeout: float) -> bool:
        self.thread.join(timeout=max(0.0, timeout))
        return not self.thread.is_alive()

    def _run(self) -> None:
        playwright = None
        browser = None
        startup_error: BaseException | None = None
        try:
            while True:
                try:
                    job = self.jobs.get(timeout=_BROWSER_IDLE_SECONDS)
                except queue.Empty:
                    if browser is not None:
                        browser.close()
                        browser = None
                    continue
                if job is None:
                    break
                with self._state_lock:
                    self._active_job = job
                try:
                    if startup_error is not None:
                        raise startup_error
                    if playwright is None:
                        try:
                            from playwright.sync_api import sync_playwright

                            playwright = sync_playwright().start()
                        except BaseException as error:
                            startup_error = error
                            raise
                    if browser is None or not browser.is_connected():
                        browser = playwright.chromium.launch(
                            executable_path=str(self.executable),
                            headless=True,
                            args=[
                                "--disable-renderer-backgrounding",
                                "--disable-background-timer-throttling",
                                "--disable-backgrounding-occluded-windows",
                                "--disable-gpu",
                                "--enable-features=CanvasDrawElement",
                                "--force-color-profile=srgb",
                                "--hide-scrollbars",
                                "--mute-audio",
                            ],
                        )
                        self.on_browser_launch()
                    metrics = self._capture(browser, job)
                except BaseException as error:
                    job.future.set_exception(error)
                    job.cancelled.set()
                    try:
                        if browser is not None and not browser.is_connected():
                            browser = None
                    except BaseException:
                        browser = None
                else:
                    job.future.set_result(metrics)
                finally:
                    with self._state_lock:
                        if self._active_job is job:
                            self._active_job = None
        finally:
            if browser is not None:
                try:
                    browser.close()
                except BaseException:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except BaseException:
                    pass

    def _capture(self, browser, job: _CaptureJob) -> _WorkerMetrics:
        context, page, cdp = self._open_capture_page(browser, job)
        if job.verifies_determinism:
            try:
                verify_non_monotonic_seek_pixels(
                    page,
                    float(page.evaluate("() => window.__hf.duration")),
                    lambda: self._capture_screenshot(cdp, job.width, job.height),
                )
            except BaseException:
                job.determinism_decision.publish(False)
                context.close()
                raise
            else:
                job.determinism_decision.publish(True)
            # Determinism probing intentionally performs non-monotonic seeks.
            # Production starts from a newly initialized document so a probe
            # cannot prime lazy animation state or mutate the capture surface.
            context.close()
            context, page, cdp = self._open_capture_page(browser, job)
        elif not job.determinism_decision.wait(job.cancelled):
            context.close()
            raise RuntimeError(
                "Editable media source did not pass non-monotonic seek verification"
            )

        try:
            fast_capture_attempt = (
                self._enable_fast_capture(page, cdp, job)
                if job.capture_mode == "auto"
                else _FastCaptureAttempt(
                    plan=None,
                    reason="capture mode explicitly requires Chrome screenshots",
                )
            )
            if fast_capture_attempt.plan is None:
                fast_capture_attempt = _FastCaptureAttempt(
                    plan=None,
                    reason=f"worker {self.index}: {fast_capture_attempt.reason}",
                )
            fast_capture_plan = fast_capture_attempt.plan
            job.capture_mode_consensus.propose(fast_capture_plan is not None)
            fast_capture = job.capture_mode_consensus.wait(job.cancelled)
            if not fast_capture and fast_capture_plan is not None:
                page.evaluate(_REMOVE_FAST_CAPTURE_CANVAS)
                fast_capture_plan = None
                capture_backend_reason = (
                    "another capture worker rejected drawElementImage"
                )
            else:
                capture_backend_reason = fast_capture_attempt.reason
            captured = 0
            seek_seconds = 0.0
            capture_seconds = 0.0
            queue_wait_seconds = 0.0
            frame_times_ms: list[float] = []
            for frame_index in job.frame_indices:
                if job.cancelled.is_set():
                    break
                frame_started = time.perf_counter()
                seconds = frame_index * job.fps_denominator / job.fps_numerator
                seek_started = time.perf_counter()
                page.evaluate(
                    SEEK_WEB_FRAME_SCRIPT,
                    seconds,
                )
                seek_seconds += time.perf_counter() - seek_started
                capture_started = time.perf_counter()
                if fast_capture:
                    try:
                        payload = self._capture_fast(page, job.width, job.height)
                        _validate_png(payload, job.width, job.height)
                        reference = (
                            fast_capture_plan.references.get(frame_index)
                            if fast_capture_plan is not None
                            else None
                        )
                        if reference is not None:
                            comparison = _compare_fast_capture(reference, payload)
                            if not comparison.accepted:
                                raise RuntimeError(
                                    "verification frame drifted from the screenshot "
                                    f"reference; {comparison.rejection_reason()}"
                                )
                    except BaseException as error:
                        raise FastCaptureFallbackRequired(
                            worker_index=self.index,
                            frame_index=frame_index,
                            reason=str(error),
                        ) from error
                else:
                    payload = self._capture_screenshot(cdp, job.width, job.height)
                capture_seconds += time.perf_counter() - capture_started
                captured += 1
                queue_wait = self._put(job, _CapturedFrame(frame_index, payload))
                if queue_wait is None:
                    break
                queue_wait_seconds += queue_wait
                frame_times_ms.append((time.perf_counter() - frame_started) * 1000)
            return _WorkerMetrics(
                captured_frames=captured,
                fast_capture=fast_capture,
                capture_backend_reason=capture_backend_reason,
                seek_seconds=seek_seconds,
                capture_seconds=capture_seconds,
                queue_wait_seconds=queue_wait_seconds,
                frame_times_ms=tuple(frame_times_ms),
            )
        finally:
            context.close()

    @staticmethod
    def _open_capture_page(browser, job: _CaptureJob):
        context = browser.new_context(
            viewport={"width": job.width, "height": job.height},
            device_scale_factor=1,
        )
        try:
            context.route(
                "http://**/*",
                lambda route: (
                    route.continue_()
                    if route.request.url.startswith(job.allowed_origin)
                    else route.abort()
                ),
            )
            context.route("https://**/*", lambda route: route.abort())
            page = context.new_page()
            page.goto(job.url, wait_until="load", timeout=15000)
            page.wait_for_function(
                """() => window.editableMedia
                    && window.editableMedia.ready instanceof Promise
                    && window.__hf
                    && typeof window.__hf.seek === "function"
                    && window.__hf.duration > 0""",
                timeout=5000,
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
                job.runtime_state,
            )
            if roundtrip != job.runtime_state:
                raise RuntimeError("Editable media runtime rejected the persisted clip state")
            cdp = context.new_cdp_session(page)
            cdp.send(
                "Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}},
            )
            return context, page, cdp
        except BaseException:
            context.close()
            raise

    def _enable_fast_capture(
        self,
        page,
        cdp,
        job: _CaptureJob,
    ) -> _FastCaptureAttempt:
        if os.environ.get("MEDIAFLOW_WEB_FAST_CAPTURE", "1").strip().lower() in {
            "0",
            "false",
            "off",
            "no",
        }:
            return _FastCaptureAttempt(
                plan=None,
                reason="MEDIAFLOW_WEB_FAST_CAPTURE disabled drawElementImage",
            )
        compatibility = page.evaluate(_FAST_CAPTURE_COMPATIBILITY)
        if compatibility.get("supported") is not True:
            return _FastCaptureAttempt(
                plan=None,
                reason=(
                    "drawElementImage compatibility rejected the page: "
                    f"{compatibility.get('reason') or 'unknown'}"
                ),
            )
        sample_indices = job.fast_capture_sample_indices
        references: dict[int, bytes] = {}
        screenshot_seconds = 0.0
        for frame_index in sample_indices:
            seconds = frame_index * job.fps_denominator / job.fps_numerator
            page.evaluate(SEEK_WEB_FRAME_SCRIPT, seconds)
            started = time.perf_counter()
            references[frame_index] = self._capture_screenshot(
                cdp,
                job.width,
                job.height,
            )
            screenshot_seconds += time.perf_counter() - started
        page.evaluate(
            _INJECT_FAST_CAPTURE_CANVAS,
            {"width": job.width, "height": job.height},
        )
        try:
            warm_frame_index, warm_reference = next(iter(references.items()))
            warm_seconds = (
                warm_frame_index
                * job.fps_denominator
                / job.fps_numerator
            )
            page.evaluate(SEEK_WEB_FRAME_SCRIPT, warm_seconds)
            warm_candidate = self._capture_fast(page, job.width, job.height)
            _validate_png(warm_candidate, job.width, job.height)
            warm_comparison = _compare_fast_capture(
                warm_reference,
                warm_candidate,
            )
            if not warm_comparison.accepted:
                raise RuntimeError(warm_comparison.rejection_reason())
            fast_capture_seconds = 0.0
            for frame_index, reference in references.items():
                seconds = frame_index * job.fps_denominator / job.fps_numerator
                page.evaluate(SEEK_WEB_FRAME_SCRIPT, seconds)
                started = time.perf_counter()
                candidate = self._capture_fast(page, job.width, job.height)
                fast_capture_seconds += time.perf_counter() - started
                _validate_png(candidate, job.width, job.height)
                comparison = _compare_fast_capture(reference, candidate)
                if not comparison.accepted:
                    raise RuntimeError(comparison.rejection_reason())
            if fast_capture_seconds >= screenshot_seconds:
                raise RuntimeError(
                    "drawElementImage was not faster than Chrome screenshot capture"
                )
        except BaseException as error:
            page.evaluate(_REMOVE_FAST_CAPTURE_CANVAS)
            return _FastCaptureAttempt(plan=None, reason=str(error))
        return _FastCaptureAttempt(
            plan=_FastCapturePlan(references=references),
            reason=(
                "every worker verified drawElementImage against Chrome screenshots"
            ),
        )

    @staticmethod
    def _capture_fast(page, width: int, height: int) -> bytes:
        data_url = page.evaluate(
            _CAPTURE_FAST_PNG,
            {"width": width, "height": height},
        )
        if not isinstance(data_url, str) or "," not in data_url:
            raise RuntimeError("drawElementImage returned an invalid PNG payload")
        return base64.b64decode(data_url.split(",", 1)[1], validate=True)

    @staticmethod
    def _capture_screenshot(cdp, width: int, height: int) -> bytes:
        result = cdp.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
                "optimizeForSpeed": True,
            },
        )
        payload = result.get("data")
        if not isinstance(payload, str):
            raise RuntimeError("Chrome returned an invalid PNG screenshot")
        decoded = base64.b64decode(payload, validate=True)
        _validate_png(decoded, width, height)
        return decoded

    @staticmethod
    def _put(
        job: _CaptureJob,
        item: _CapturedFrame,
    ) -> float | None:
        started = time.perf_counter()
        while not job.cancelled.is_set():
            try:
                job.output.put(item, timeout=0.1)
            except queue.Full:
                continue
            return time.perf_counter() - started
        return None


class WebCaptureEngine:
    def __init__(self, executable: Path) -> None:
        self.executable = executable.resolve()
        self._lock = threading.Lock()
        self._diagnostic_lock = threading.Lock()
        self._browser_launches = 0
        self._render_count = 0
        self._failed_render_count = 0
        self._last_metrics: WebCaptureMetrics | None = None
        self._last_failure: WebCaptureFailure | None = None
        self._validated_render_states: set[str] = set()
        self._workers = [
            _BrowserWorker(
                executable=self.executable,
                index=index,
                on_browser_launch=self._record_browser_launch,
            )
            for index in range(_configured_worker_limit())
        ]

    def render_frames(
        self,
        *,
        url: str,
        allowed_origin: str,
        width: int,
        height: int,
        fps_numerator: int,
        fps_denominator: int,
        runtime_state: dict[str, Any],
        determinism_key: str,
        frame_count: int,
        on_frame: Callable[[bytes], None],
        on_progress: Callable[[int], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
        capture_mode: WebCaptureMode = "auto",
        fallback_reason: str | None = None,
    ) -> WebCaptureMetrics:
        if capture_mode not in {"auto", "screenshot"}:
            raise ValueError(f"Unsupported editable media capture mode: {capture_mode}")
        started = time.perf_counter()
        sizing = _resolve_worker_count(
            frame_count=frame_count,
            width=width,
            height=height,
            limit=len(self._workers),
        )
        worker_count = sizing.workers
        cancelled = threading.Event()
        outputs = [
            queue.Queue[_CapturedFrame](maxsize=_FRAME_QUEUE_DEPTH)
            for _ in range(worker_count)
        ]
        futures: list[Future[_WorkerMetrics]] = []
        capture_mode_consensus = _CaptureModeConsensus(
            worker_count=worker_count,
            ready=threading.Event(),
            lock=threading.Lock(),
        )
        fast_capture_samples = _fast_capture_sample_indices(
            frame_count=frame_count,
            worker_count=worker_count,
        )
        with self._lock:
            verifies_determinism = (
                determinism_key not in self._validated_render_states
            )
            determinism_decision = _BooleanDecision(ready=threading.Event())
            if not verifies_determinism:
                determinism_decision.publish(True)
            for worker_index in range(worker_count):
                future: Future[_WorkerMetrics] = Future()
                futures.append(future)
                self._workers[worker_index].submit(
                    _CaptureJob(
                        url=url,
                        allowed_origin=allowed_origin,
                        width=width,
                        height=height,
                        fps_numerator=fps_numerator,
                        fps_denominator=fps_denominator,
                        runtime_state=runtime_state,
                        frame_indices=range(worker_index, frame_count, worker_count),
                        output=outputs[worker_index],
                        cancelled=cancelled,
                        future=future,
                        determinism_decision=determinism_decision,
                        verifies_determinism=verifies_determinism and worker_index == 0,
                        capture_mode_consensus=capture_mode_consensus,
                        fast_capture_sample_indices=tuple(
                            frame_index
                            for frame_index in fast_capture_samples
                            if frame_index % worker_count == worker_index
                        ),
                        capture_mode=capture_mode,
                    )
                )
            try:
                for expected_index in range(frame_count):
                    if check_cancelled is not None:
                        check_cancelled()
                    output = outputs[expected_index % worker_count]
                    item = self._next_frame(
                        output,
                        futures,
                        cancelled,
                        check_cancelled=check_cancelled,
                    )
                    if item.index != expected_index:
                        raise RuntimeError(
                            "Parallel editable media capture returned frames out of order"
                        )
                    on_frame(item.payload)
                    if on_progress is not None:
                        on_progress(expected_index + 1)
                worker_metrics = [future.result() for future in futures]
                self._validated_render_states.add(determinism_key)
            except BaseException as error:
                cancelled.set()
                self._record_render_failure(
                    capture_mode=capture_mode,
                    error=error,
                    elapsed_seconds=time.perf_counter() - started,
                )
                raise

        frame_times_ms = [
            value
            for worker in worker_metrics
            for value in worker.frame_times_ms
        ]
        metrics = WebCaptureMetrics(
            worker_count=worker_count,
            frame_count=frame_count,
            captured_frames=sum(item.captured_frames for item in worker_metrics),
            fast_capture_workers=sum(item.fast_capture for item in worker_metrics),
            capture_backend=(
                "drawelement"
                if all(item.fast_capture for item in worker_metrics)
                else "screenshot"
            ),
            capture_backend_reason="; ".join(
                dict.fromkeys(
                    item.capture_backend_reason for item in worker_metrics
                )
            ),
            fallback_reason=fallback_reason,
            sizing=sizing,
            seek_seconds=sum(item.seek_seconds for item in worker_metrics),
            capture_seconds=sum(item.capture_seconds for item in worker_metrics),
            queue_wait_seconds=sum(item.queue_wait_seconds for item in worker_metrics),
            frame_time_p50_ms=_percentile(frame_times_ms, 0.50),
            frame_time_p95_ms=_percentile(frame_times_ms, 0.95),
            elapsed_seconds=time.perf_counter() - started,
        )
        with self._diagnostic_lock:
            self._render_count += 1
            self._last_metrics = metrics
        return metrics

    def diagnostics(self) -> WebCaptureDiagnostics:
        with self._diagnostic_lock:
            return WebCaptureDiagnostics(
                browser_launches=self._browser_launches,
                render_count=self._render_count,
                failed_render_count=self._failed_render_count,
                last_metrics=self._last_metrics,
                last_failure=self._last_failure,
            )

    def close(self) -> None:
        for worker in self._workers:
            worker.stop()
        deadline = time.monotonic() + 15.0
        for worker in self._workers:
            worker.join(deadline - time.monotonic())

    def _record_browser_launch(self) -> None:
        with self._diagnostic_lock:
            self._browser_launches += 1

    def _record_render_failure(
        self,
        *,
        capture_mode: WebCaptureMode,
        error: BaseException,
        elapsed_seconds: float,
    ) -> None:
        with self._diagnostic_lock:
            self._failed_render_count += 1
            self._last_failure = WebCaptureFailure(
                capture_mode=capture_mode,
                error_type=type(error).__name__,
                message=str(error),
                elapsed_seconds=elapsed_seconds,
            )

    @staticmethod
    def _next_frame(
        output: queue.Queue[_CapturedFrame],
        futures: list[Future[_WorkerMetrics]],
        cancelled: threading.Event,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> _CapturedFrame:
        while True:
            if check_cancelled is not None:
                check_cancelled()
            try:
                return output.get(timeout=0.1)
            except queue.Empty:
                for future in futures:
                    if not future.done():
                        continue
                    failure = future.exception()
                    if failure is not None:
                        raise failure from None
                if cancelled.is_set():
                    raise RuntimeError("Editable media capture was cancelled") from None


def _decode_png_bgra(payload: bytes):
    import cv2
    import numpy as np

    image = cv2.imdecode(
        np.frombuffer(payload, dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    if image is None:
        return None
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    if image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return image


def _compare_fast_capture(left: bytes, right: bytes) -> _FastCaptureComparison:
    import cv2
    import numpy as np

    left_image = _decode_png_bgra(left)
    right_image = _decode_png_bgra(right)
    if left_image is None or right_image is None:
        return _FastCaptureComparison(
            psnr_db=0.0,
            blurred_psnr_db=0.0,
            mean_absolute_error=float("inf"),
            blurred_channel_error=255,
            alpha_equal=False,
        )
    if left_image.shape != right_image.shape:
        return _FastCaptureComparison(
            psnr_db=0.0,
            blurred_psnr_db=0.0,
            mean_absolute_error=float("inf"),
            blurred_channel_error=255,
            alpha_equal=False,
        )
    difference = cv2.absdiff(left_image, right_image)
    blurred_left = cv2.GaussianBlur(left_image, (5, 5), 1.2)
    blurred_right = cv2.GaussianBlur(right_image, (5, 5), 1.2)
    blurred_difference = cv2.absdiff(blurred_left, blurred_right)
    return _FastCaptureComparison(
        psnr_db=float(cv2.PSNR(left_image, right_image)),
        blurred_psnr_db=float(cv2.PSNR(blurred_left, blurred_right)),
        mean_absolute_error=float(np.mean(difference[:, :, :3])),
        blurred_channel_error=int(np.max(blurred_difference[:, :, :3])),
        alpha_equal=bool(
            np.array_equal(left_image[:, :, 3], right_image[:, :, 3])
        ),
    )
def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _validate_png(payload: bytes, width: int, height: int) -> None:
    if (
        len(payload) < 24
        or payload[:8] != _PNG_SIGNATURE
        or payload[12:16] != b"IHDR"
    ):
        raise RuntimeError("Capture backend returned an invalid PNG frame")
    actual_width, actual_height = struct.unpack(">II", payload[16:24])
    if (actual_width, actual_height) != (width, height):
        raise RuntimeError(
            "Capture backend returned the wrong frame size: "
            f"{actual_width}x{actual_height}, expected {width}x{height}"
        )


def _fast_capture_sample_indices(
    *,
    frame_count: int,
    worker_count: int,
) -> tuple[int, ...]:
    if frame_count <= 0 or worker_count <= 0:
        raise ValueError("Editable media capture needs positive frame and worker counts")
    samples: set[int] = set()
    for worker_index in range(min(frame_count, worker_count)):
        local_frames = range(worker_index, frame_count, worker_count)
        samples.add(local_frames.start)
        samples.add(
            local_frames.start
            + round(max(0, len(local_frames) - 1) * 0.95) * local_frames.step
        )
    target_count = min(frame_count, 4 + 2 * max(0, worker_count - 1))
    for fraction in (0.25, 0.5, 0.75, 0.95, 1.0):
        if len(samples) >= target_count:
            break
        samples.add(round((frame_count - 1) * fraction))
    if len(samples) < target_count:
        for frame_index in range(frame_count):
            samples.add(frame_index)
            if len(samples) >= target_count:
                break
    return tuple(sorted(samples))


def _configured_worker_limit() -> int:
    configured = os.environ.get("MEDIAFLOW_WEB_WORKERS")
    if configured:
        try:
            return max(1, min(8, int(configured)))
        except ValueError as error:
            raise ValueError("MEDIAFLOW_WEB_WORKERS must be an integer from 1 to 8") from error
    cpus = os.cpu_count() or 1
    return max(1, min(4, math.ceil(cpus / 4)))


def _resolve_worker_count(
    *,
    frame_count: int,
    width: int,
    height: int,
    limit: int,
) -> WebCaptureWorkerSizing:
    available_memory = available_physical_memory_bytes()
    estimated_worker_bytes = 256 * 1024**2 + width * height * 4 * 6
    by_memory = max(
        1,
        math.floor(available_memory * 0.5 / estimated_worker_bytes),
    )
    by_work = 1 if frame_count < 90 else max(1, math.ceil(frame_count / 150))
    pixels = width * height
    by_pixels = 2 if pixels > 8_000_000 else 3 if pixels > 4_000_000 else limit
    limits: tuple[tuple[WebWorkerSizingBound, int], ...] = (
        ("worker_limit", max(1, limit)),
        ("work", by_work),
        ("memory", by_memory),
        ("pixels", by_pixels),
    )
    workers = max(1, min(value for _name, value in limits))
    bound_by = next(name for name, value in limits if value == workers)
    return WebCaptureWorkerSizing(
        workers=workers,
        bound_by=bound_by,
        worker_limit=max(1, limit),
        work_limit=by_work,
        memory_limit=by_memory,
        pixel_limit=by_pixels,
        available_memory_bytes=available_memory,
        estimated_worker_bytes=estimated_worker_bytes,
    )


_ENGINES: dict[Path, WebCaptureEngine] = {}
_ENGINES_LOCK = threading.Lock()


def get_web_capture_engine(executable: Path) -> WebCaptureEngine:
    resolved = executable.resolve()
    with _ENGINES_LOCK:
        engine = _ENGINES.get(resolved)
        if engine is None:
            engine = WebCaptureEngine(resolved)
            _ENGINES[resolved] = engine
        return engine


def web_capture_diagnostics(executable: Path) -> WebCaptureDiagnostics:
    return get_web_capture_engine(executable).diagnostics()


@atexit.register
def shutdown_web_capture_engines() -> None:
    with _ENGINES_LOCK:
        engines = list(_ENGINES.values())
        _ENGINES.clear()
    for engine in engines:
        engine.close()
