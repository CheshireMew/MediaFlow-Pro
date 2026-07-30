from __future__ import annotations

import atexit
import base64
import math
import os
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediaflow.infrastructure.web_browser import verify_non_monotonic_seek_pixels

_BROWSER_IDLE_SECONDS = 30.0
_FRAME_QUEUE_DEPTH = 2
_FAST_CAPTURE_VERIFY_DB = 48.0

_SEEK_FRAME = """
async time => {
    await window.editableMedia.ready;
    await window.__hf.seek(time);
    await new Promise(resolve => requestAnimationFrame(() => resolve()));
    const root = document.querySelector("[data-composition-id]");
    if (!root) throw new Error("Editable media composition root is missing");
}
"""

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
class WebCaptureMetrics:
    worker_count: int
    frame_count: int
    captured_frames: int
    fast_capture_workers: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class WebCaptureDiagnostics:
    browser_launches: int
    render_count: int
    last_metrics: WebCaptureMetrics | None


@dataclass(frozen=True, slots=True)
class _CapturedFrame:
    index: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class _WorkerMetrics:
    captured_frames: int
    fast_capture: bool


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
    fast_capture_decision: _BooleanDecision
    probes_fast_capture: bool


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
        self.thread = threading.Thread(
            target=self._run,
            name=f"mediaflow-web-capture-{index}",
            daemon=True,
        )
        self.thread.start()

    def submit(self, job: _CaptureJob) -> None:
        self.jobs.put(job)

    def stop(self) -> None:
        self.jobs.put(None)

    def join(self) -> None:
        self.thread.join()

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
            if job.verifies_determinism:
                try:
                    verify_non_monotonic_seek_pixels(
                        page,
                        float(page.evaluate("() => window.__hf.duration")),
                        lambda: self._capture_screenshot(cdp),
                    )
                except BaseException:
                    job.determinism_decision.publish(False)
                    raise
                else:
                    job.determinism_decision.publish(True)
            elif not job.determinism_decision.wait(job.cancelled):
                raise RuntimeError(
                    "Editable media source did not pass non-monotonic seek verification"
                )
            if job.probes_fast_capture:
                fast_capture = self._enable_fast_capture(page, cdp, job)
                job.fast_capture_decision.publish(fast_capture)
            else:
                fast_capture = job.fast_capture_decision.wait(job.cancelled)
                if fast_capture:
                    compatibility = page.evaluate(_FAST_CAPTURE_COMPATIBILITY)
                    fast_capture = compatibility.get("supported") is True
                    if fast_capture:
                        page.evaluate(
                            _INJECT_FAST_CAPTURE_CANVAS,
                            {"width": job.width, "height": job.height},
                        )
            captured = 0
            for frame_index in job.frame_indices:
                if job.cancelled.is_set():
                    break
                seconds = frame_index * job.fps_denominator / job.fps_numerator
                page.evaluate(
                    _SEEK_FRAME,
                    seconds,
                )
                payload = (
                    self._capture_fast(page, job.width, job.height)
                    if fast_capture
                    else self._capture_screenshot(cdp)
                )
                captured += 1
                if not self._put(job, _CapturedFrame(frame_index, payload)):
                    break
            return _WorkerMetrics(
                captured_frames=captured,
                fast_capture=fast_capture,
            )
        finally:
            context.close()

    def _enable_fast_capture(self, page, cdp, job: _CaptureJob) -> bool:
        if os.environ.get("MEDIAFLOW_WEB_FAST_CAPTURE", "1").strip().lower() in {
            "0",
            "false",
            "off",
            "no",
        }:
            return False
        compatibility = page.evaluate(_FAST_CAPTURE_COMPATIBILITY)
        if compatibility.get("supported") is not True:
            return False
        sample_indices = sorted(
            {
                job.frame_indices.start,
                job.frame_indices.start
                + (
                    max(0, len(job.frame_indices) - 1)
                    // 2
                    * job.frame_indices.step
                ),
                job.frame_indices.start
                + max(0, len(job.frame_indices) - 1) * job.frame_indices.step,
            }
        )
        references: list[bytes] = []
        screenshot_seconds = 0.0
        for frame_index in sample_indices:
            seconds = frame_index * job.fps_denominator / job.fps_numerator
            page.evaluate(_SEEK_FRAME, seconds)
            started = time.perf_counter()
            references.append(self._capture_screenshot(cdp))
            screenshot_seconds += time.perf_counter() - started
        page.evaluate(
            _INJECT_FAST_CAPTURE_CANVAS,
            {"width": job.width, "height": job.height},
        )
        try:
            fast_capture_seconds = 0.0
            for frame_index, reference in zip(sample_indices, references, strict=True):
                seconds = frame_index * job.fps_denominator / job.fps_numerator
                page.evaluate(_SEEK_FRAME, seconds)
                started = time.perf_counter()
                candidate = self._capture_fast(page, job.width, job.height)
                fast_capture_seconds += time.perf_counter() - started
                if _png_psnr(reference, candidate) < _FAST_CAPTURE_VERIFY_DB:
                    raise RuntimeError("drawElementImage output did not match the screenshot path")
            if fast_capture_seconds >= screenshot_seconds:
                raise RuntimeError("drawElementImage was not faster than Chrome screenshot capture")
        except BaseException:
            page.evaluate(_REMOVE_FAST_CAPTURE_CANVAS)
            return False
        return True

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
    def _capture_screenshot(cdp) -> bytes:
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
        return base64.b64decode(payload, validate=True)

    @staticmethod
    def _put(
        job: _CaptureJob,
        item: _CapturedFrame,
    ) -> bool:
        while not job.cancelled.is_set():
            try:
                job.output.put(item, timeout=0.1)
            except queue.Full:
                continue
            return True
        return False


class WebCaptureEngine:
    def __init__(self, executable: Path) -> None:
        self.executable = executable.resolve()
        self._lock = threading.Lock()
        self._diagnostic_lock = threading.Lock()
        self._browser_launches = 0
        self._render_count = 0
        self._last_metrics: WebCaptureMetrics | None = None
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
    ) -> WebCaptureMetrics:
        started = time.perf_counter()
        worker_count = _resolve_worker_count(
            frame_count=frame_count,
            width=width,
            height=height,
            limit=len(self._workers),
        )
        cancelled = threading.Event()
        outputs = [
            queue.Queue[_CapturedFrame](maxsize=_FRAME_QUEUE_DEPTH)
            for _ in range(worker_count)
        ]
        futures: list[Future[_WorkerMetrics]] = []
        fast_capture_decision = _BooleanDecision(ready=threading.Event())
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
                        fast_capture_decision=fast_capture_decision,
                        probes_fast_capture=worker_index == 0,
                    )
                )
            try:
                for expected_index in range(frame_count):
                    if check_cancelled is not None:
                        check_cancelled()
                    output = outputs[expected_index % worker_count]
                    item = self._next_frame(output, futures, cancelled)
                    if item.index != expected_index:
                        raise RuntimeError(
                            "Parallel editable media capture returned frames out of order"
                        )
                    on_frame(item.payload)
                    if on_progress is not None:
                        on_progress(expected_index + 1)
                worker_metrics = [future.result() for future in futures]
                self._validated_render_states.add(determinism_key)
            except BaseException:
                cancelled.set()
                raise

        metrics = WebCaptureMetrics(
            worker_count=worker_count,
            frame_count=frame_count,
            captured_frames=sum(item.captured_frames for item in worker_metrics),
            fast_capture_workers=sum(item.fast_capture for item in worker_metrics),
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
                last_metrics=self._last_metrics,
            )

    def close(self) -> None:
        for worker in self._workers:
            worker.stop()
        for worker in self._workers:
            worker.join()

    def _record_browser_launch(self) -> None:
        with self._diagnostic_lock:
            self._browser_launches += 1

    @staticmethod
    def _next_frame(
        output: queue.Queue[_CapturedFrame],
        futures: list[Future[_WorkerMetrics]],
        cancelled: threading.Event,
    ) -> _CapturedFrame:
        while True:
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


def _png_psnr(left: bytes, right: bytes) -> float:
    import cv2
    import numpy as np

    left_image = cv2.imdecode(np.frombuffer(left, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    right_image = cv2.imdecode(np.frombuffer(right, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if left_image is None or right_image is None:
        return 0.0
    if left_image.ndim == 2:
        left_image = cv2.cvtColor(left_image, cv2.COLOR_GRAY2BGRA)
    elif left_image.shape[2] == 3:
        left_image = cv2.cvtColor(left_image, cv2.COLOR_BGR2BGRA)
    if right_image.ndim == 2:
        right_image = cv2.cvtColor(right_image, cv2.COLOR_GRAY2BGRA)
    elif right_image.shape[2] == 3:
        right_image = cv2.cvtColor(right_image, cv2.COLOR_BGR2BGRA)
    if left_image.shape != right_image.shape:
        return 0.0
    return float(cv2.PSNR(left_image, right_image))


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
) -> int:
    if frame_count < 90:
        return 1
    by_work = max(1, math.ceil(frame_count / 150))
    pixels = width * height
    by_pixels = 2 if pixels > 8_000_000 else 3 if pixels > 4_000_000 else limit
    return max(1, min(limit, by_work, by_pixels))


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
