from __future__ import annotations

import base64
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
from mediaflow.infrastructure.web_capture_models import (
    FastCaptureFallbackRequired,
    WebCaptureMode,
    WebFrameCaptureError,
    _CapturedFrame,
    _FastCaptureAttempt,
    _FastCapturePlan,
    _FastFrameCaptureError,
    _WorkerMetrics,
)
from mediaflow.infrastructure.web_capture_page import (
    _CAPTURE_FAST_PNG,
    _FAST_CAPTURE_COMPATIBILITY,
    _INJECT_FAST_CAPTURE_CANVAS,
    _REMOVE_FAST_CAPTURE_CANVAS,
    _browser_was_closed,
    _page_can_be_replaced,
    _retry_capture_url,
    _seek_frame,
)
from mediaflow.infrastructure.web_capture_quality import (
    _compare_fast_capture,
    _validate_png,
)
from mediaflow.infrastructure.web_capture_scheduler import (
    _BooleanDecision,
    _BrowserPoolGeneration,
    _CaptureModeConsensus,
    _FrameScheduler,
)

_BROWSER_IDLE_SECONDS = 30.0


@dataclass(slots=True)
class _CaptureJob:
    url: str
    allowed_origin: str
    width: int
    height: int
    fps_numerator: int
    fps_denominator: int
    runtime_state: dict[str, Any]
    scheduler: _FrameScheduler
    output: queue.Queue[_CapturedFrame]
    cancelled: threading.Event
    future: Future[_WorkerMetrics]
    determinism_decision: _BooleanDecision
    verifies_determinism: bool
    capture_mode_consensus: _CaptureModeConsensus
    fast_capture_sample_indices: tuple[int, ...]
    capture_mode: WebCaptureMode
    retry_limit: int


class _BrowserWorker:
    def __init__(
        self,
        *,
        executable: Path,
        index: int,
        on_browser_launch: Callable[[], None],
        browser_pool_generation: _BrowserPoolGeneration,
    ) -> None:
        self.executable = executable
        self.index = index
        self.on_browser_launch = on_browser_launch
        self.browser_pool_generation = browser_pool_generation
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
        browser_generation = -1
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

                    def replace_browser(playwright_instance=playwright):
                        nonlocal browser, browser_generation
                        if browser is not None:
                            try:
                                browser.close()
                            except BaseException:
                                pass
                            browser = None
                        while True:
                            target_generation = self.browser_pool_generation.current
                            candidate = playwright_instance.chromium.launch(
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
                            if self.browser_pool_generation.current == target_generation:
                                browser = candidate
                                browser_generation = target_generation
                                return browser, browser_generation
                            candidate.close()

                    if (
                        browser is None
                        or not browser.is_connected()
                        or browser_generation != self.browser_pool_generation.current
                    ):
                        replace_browser()
                    metrics = self._capture(
                        browser,
                        browser_generation,
                        replace_browser,
                        job,
                    )
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

    def _capture(
        self,
        browser,
        browser_generation: int,
        replace_browser: Callable[[], tuple[Any, int]],
        job: _CaptureJob,
    ) -> _WorkerMetrics:
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
            raise RuntimeError("Editable media source did not pass non-monotonic seek verification")

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
                capture_backend_reason = "another capture worker rejected drawElementImage"
            else:
                capture_backend_reason = fast_capture_attempt.reason
            captured = 0
            seek_seconds = 0.0
            capture_seconds = 0.0
            queue_wait_seconds = 0.0
            frame_times_ms: list[float] = []
            retry_count = 0
            page_replacement_count = 0
            browser_replacement_count = 0
            readiness_wait_seconds = 0.0
            timeout_labels: set[str] = set()
            while True:
                if job.cancelled.is_set():
                    break
                if browser_generation != self.browser_pool_generation.current:
                    context.close()
                    browser, browser_generation = replace_browser()
                    context, page, cdp = self._open_capture_page(browser, job)
                    if fast_capture:
                        page.evaluate(
                            _INJECT_FAST_CAPTURE_CANVAS,
                            {"width": job.width, "height": job.height},
                        )
                    browser_replacement_count += 1
                lease = job.scheduler.lease(self.index)
                if lease is None:
                    break
                frame_index = lease.index
                frame_started = time.perf_counter()
                seconds = frame_index * job.fps_denominator / job.fps_numerator
                try:
                    seek_started = time.perf_counter()
                    readiness = _seek_frame(page, seconds, frame_index)
                    seek_seconds += time.perf_counter() - seek_started
                    readiness_wait_seconds += float(readiness.get("wait_ms") or 0.0) / 1000
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
                            raise _FastFrameCaptureError(str(error)) from error
                    else:
                        payload = self._capture_screenshot(cdp, job.width, job.height)
                    capture_seconds += time.perf_counter() - capture_started
                    queue_wait = self._put(job, _CapturedFrame(frame_index, payload))
                    if queue_wait is None:
                        job.scheduler.return_frame(self.index, frame_index)
                        break
                    job.scheduler.complete(self.index, frame_index)
                    captured += 1
                    queue_wait_seconds += queue_wait
                    frame_times_ms.append((time.perf_counter() - frame_started) * 1000)
                except BaseException as error:
                    job.scheduler.return_frame(self.index, frame_index)
                    retryable = (
                        isinstance(error, WebFrameCaptureError) and error.retryable
                    ) or _page_can_be_replaced(error)
                    if isinstance(error, _FastFrameCaptureError):
                        raise FastCaptureFallbackRequired(
                            worker_index=self.index,
                            frame_index=frame_index,
                            reason=str(error),
                        ) from error
                    if not retryable or lease.attempt > job.retry_limit:
                        raise RuntimeError(
                            "Editable media frame capture failed: "
                            f"frame={frame_index}, attempt={lease.attempt}, error={error}"
                        ) from error
                    retry_count += 1
                    if isinstance(error, WebFrameCaptureError) and error.code == "frame_task_timeout":
                        timeout_labels.add(error.label or "unknown")
                    context.close()
                    browser_connected = False
                    try:
                        browser_connected = browser.is_connected()
                    except BaseException:
                        pass
                    if not browser_connected or _browser_was_closed(error):
                        self.browser_pool_generation.invalidate(browser_generation)
                    if browser_generation != self.browser_pool_generation.current:
                        browser, browser_generation = replace_browser()
                        browser_replacement_count += 1
                    else:
                        page_replacement_count += 1
                    context, page, cdp = self._open_capture_page(
                        browser,
                        job,
                        url=_retry_capture_url(
                            job.url,
                            frame_index=frame_index,
                            attempt=lease.attempt + 1,
                        ),
                    )
                    if fast_capture:
                        page.evaluate(
                            _INJECT_FAST_CAPTURE_CANVAS,
                            {"width": job.width, "height": job.height},
                        )
            return _WorkerMetrics(
                captured_frames=captured,
                fast_capture=fast_capture,
                capture_backend_reason=capture_backend_reason,
                seek_seconds=seek_seconds,
                capture_seconds=capture_seconds,
                queue_wait_seconds=queue_wait_seconds,
                frame_times_ms=tuple(frame_times_ms),
                retry_count=retry_count,
                page_replacement_count=page_replacement_count,
                browser_replacement_count=browser_replacement_count,
                readiness_wait_seconds=readiness_wait_seconds,
                timeout_labels=tuple(sorted(timeout_labels)),
            )
        finally:
            context.close()

    @staticmethod
    def _open_capture_page(browser, job: _CaptureJob, *, url: str | None = None):
        context = browser.new_context(
            viewport={"width": job.width, "height": job.height},
            device_scale_factor=1,
        )
        try:
            context.route(
                "http://**/*",
                lambda route: (
                    route.continue_() if route.request.url.startswith(job.allowed_origin) else route.abort()
                ),
            )
            context.route("https://**/*", lambda route: route.abort())
            page = context.new_page()
            page.goto(url or job.url, wait_until="load", timeout=15000)
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
        for frame_index in sample_indices:
            seconds = frame_index * job.fps_denominator / job.fps_numerator
            _seek_frame(page, seconds, frame_index)
            references[frame_index] = self._capture_screenshot(
                cdp,
                job.width,
                job.height,
            )
        page.evaluate(
            _INJECT_FAST_CAPTURE_CANVAS,
            {"width": job.width, "height": job.height},
        )
        try:
            warm_frame_index, warm_reference = next(iter(references.items()))
            warm_seconds = warm_frame_index * job.fps_denominator / job.fps_numerator
            _seek_frame(page, warm_seconds, warm_frame_index)
            warm_candidate = self._capture_fast(page, job.width, job.height)
            _validate_png(warm_candidate, job.width, job.height)
            warm_comparison = _compare_fast_capture(
                warm_reference,
                warm_candidate,
            )
            if not warm_comparison.accepted:
                raise RuntimeError(warm_comparison.rejection_reason())
            for frame_index, reference in references.items():
                seconds = frame_index * job.fps_denominator / job.fps_numerator
                _seek_frame(page, seconds, frame_index)
                candidate = self._capture_fast(page, job.width, job.height)
                _validate_png(candidate, job.width, job.height)
                comparison = _compare_fast_capture(reference, candidate)
                if not comparison.accepted:
                    raise RuntimeError(comparison.rejection_reason())
        except BaseException as error:
            page.evaluate(_REMOVE_FAST_CAPTURE_CANVAS)
            return _FastCaptureAttempt(plan=None, reason=str(error))
        return _FastCaptureAttempt(
            plan=_FastCapturePlan(references=references),
            reason=("every worker verified drawElementImage against Chrome screenshots"),
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
