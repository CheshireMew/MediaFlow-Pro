from __future__ import annotations

import atexit
import queue
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, wait
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediaflow.infrastructure.web_capture_models import (
    FastCaptureFallbackRequired,
    WebCaptureDiagnostics,
    WebCaptureFailure,
    WebCaptureMetrics,
    WebCaptureMode,
    WebCaptureWorkerSizing,
    WebFrameCaptureError,
    WebWorkerSizingBound,
    _CapturedFrame,
    _WorkerMetrics,
)
from mediaflow.infrastructure.web_capture_pool import IdleResourcePool
from mediaflow.infrastructure.web_capture_quality import (
    _compare_fast_capture,
    _fast_capture_sample_indices,
    _percentile,
    _validate_png,
)
from mediaflow.infrastructure.web_capture_scheduler import (
    _BooleanDecision,
    _BrowserPoolGeneration,
    _CaptureModeConsensus,
    _configured_worker_limit,
    _FrameScheduler,
    _resolve_worker_count,
)
from mediaflow.infrastructure.web_capture_worker import (
    _BrowserWorker,
    _CaptureJob,
)

__all__ = (
    "FastCaptureFallbackRequired",
    "WebCaptureDiagnostics",
    "WebCaptureEngine",
    "WebCaptureMetrics",
    "WebCaptureMode",
    "WebCaptureWorkerSizing",
    "WebFrameCaptureError",
    "WebWorkerSizingBound",
    "_BrowserPoolGeneration",
    "_BrowserWorker",
    "_FrameScheduler",
    "_compare_fast_capture",
    "_fast_capture_sample_indices",
    "_resolve_worker_count",
    "_validate_png",
    "get_web_capture_engine",
    "release_web_capture_engine",
    "shutdown_web_capture_engines",
    "web_capture_diagnostics",
)

_FRAME_QUEUE_DEPTH = 2


@dataclass(slots=True)
class _CaptureRun:
    started: float
    sizing: WebCaptureWorkerSizing
    scheduler: _FrameScheduler
    cancelled: threading.Event
    output: queue.Queue[_CapturedFrame]
    futures: list[Future[_WorkerMetrics]]


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
        self._browser_pool_generation = _BrowserPoolGeneration()
        self._workers = [
            _BrowserWorker(
                executable=self.executable,
                index=index,
                on_browser_launch=self._record_browser_launch,
                browser_pool_generation=self._browser_pool_generation,
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
        retry_limit: int = 1,
        start_frame: int = 0,
    ) -> WebCaptureMetrics:
        self._validate_request(
            capture_mode=capture_mode,
            retry_limit=retry_limit,
            start_frame=start_frame,
        )
        run = self._create_run(
            frame_count,
            width,
            height,
            start_frame=start_frame,
        )
        with self._render_lock(check_cancelled):
            try:
                self._dispatch_workers(
                    run,
                    url=url,
                    allowed_origin=allowed_origin,
                    width=width,
                    height=height,
                    fps_numerator=fps_numerator,
                    fps_denominator=fps_denominator,
                    runtime_state=runtime_state,
                    determinism_key=determinism_key,
                    frame_count=frame_count,
                    capture_mode=capture_mode,
                    retry_limit=retry_limit,
                    start_frame=start_frame,
                )
                self._consume_frames(
                    run,
                    frame_count,
                    start_frame=start_frame,
                    on_frame=on_frame,
                    on_progress=on_progress,
                    check_cancelled=check_cancelled,
                )
                worker_metrics = self._await_worker_metrics(
                    run.futures,
                    check_cancelled=check_cancelled,
                )
                self._validated_render_states.add(determinism_key)
            except BaseException as error:
                run.cancelled.set()
                self._record_render_failure(
                    capture_mode=capture_mode,
                    error=error,
                    elapsed_seconds=(time.perf_counter() - run.started),
                )
                raise
        return self._record_metrics(
            run,
            frame_count,
            worker_metrics,
            fallback_reason=fallback_reason,
        )

    def prewarm(self, *, worker_count: int = 1) -> None:
        """Start a bounded number of Chromium workers before the first capture."""

        bounded_count = max(1, min(worker_count, len(self._workers)))
        futures = [worker.prewarm() for worker in self._workers[:bounded_count]]
        for future in futures:
            future.result(timeout=30)

    @staticmethod
    def _validate_request(
        *,
        capture_mode: WebCaptureMode,
        retry_limit: int,
        start_frame: int,
    ) -> None:
        if capture_mode not in {"auto", "screenshot"}:
            raise ValueError(f"Unsupported editable media capture mode: {capture_mode}")
        if not 0 <= retry_limit <= 3:
            raise ValueError("Editable media frame retry limit must be between 0 and 3")
        if start_frame < 0:
            raise ValueError("Editable media capture start frame cannot be negative")

    def _create_run(
        self,
        frame_count: int,
        width: int,
        height: int,
        *,
        start_frame: int,
    ) -> _CaptureRun:
        sizing = _resolve_worker_count(
            frame_count=frame_count,
            width=width,
            height=height,
            limit=len(self._workers),
        )
        cancelled = threading.Event()
        return _CaptureRun(
            started=time.perf_counter(),
            sizing=sizing,
            scheduler=_FrameScheduler(
                frame_count,
                sizing.workers,
                start_frame=start_frame,
            ),
            cancelled=cancelled,
            output=queue.Queue[_CapturedFrame](
                maxsize=max(
                    1,
                    sizing.workers * _FRAME_QUEUE_DEPTH,
                )
            ),
            futures=[],
        )

    def _dispatch_workers(
        self,
        run: _CaptureRun,
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
        capture_mode: WebCaptureMode,
        retry_limit: int,
        start_frame: int,
    ) -> None:
        worker_count = run.sizing.workers
        consensus = _CaptureModeConsensus(
            worker_count=worker_count,
            ready=threading.Event(),
            lock=threading.Lock(),
        )
        sample_indices = tuple(
            start_frame + frame
            for frame in _fast_capture_sample_indices(
                frame_count=frame_count,
                worker_count=worker_count,
            )
        )
        verifies = determinism_key not in self._validated_render_states
        decision = _BooleanDecision(ready=threading.Event())
        if not verifies:
            decision.publish(True)
        for worker_index in range(worker_count):
            future: Future[_WorkerMetrics] = Future()
            run.futures.append(future)
            sample_start = start_frame + frame_count * worker_index // worker_count
            sample_end = start_frame + frame_count * (worker_index + 1) // worker_count
            self._workers[worker_index].submit(
                _CaptureJob(
                    url=url,
                    allowed_origin=allowed_origin,
                    width=width,
                    height=height,
                    fps_numerator=fps_numerator,
                    fps_denominator=fps_denominator,
                    runtime_state=runtime_state,
                    scheduler=run.scheduler,
                    output=run.output,
                    cancelled=run.cancelled,
                    future=future,
                    determinism_decision=decision,
                    verifies_determinism=(verifies and worker_index == 0),
                    capture_mode_consensus=consensus,
                    fast_capture_sample_indices=tuple(
                        frame for frame in sample_indices if sample_start <= frame < sample_end
                    ),
                    capture_mode=capture_mode,
                    retry_limit=retry_limit,
                )
            )

    def _consume_frames(
        self,
        run: _CaptureRun,
        frame_count: int,
        *,
        start_frame: int,
        on_frame: Callable[[bytes], None],
        on_progress: Callable[[int], None] | None,
        check_cancelled: Callable[[], None] | None,
    ) -> None:
        ordered: dict[int, _CapturedFrame] = {}
        expected_frames = range(
            start_frame,
            start_frame + frame_count,
        )
        for completed_count, expected_index in enumerate(
            expected_frames,
            start=1,
        ):
            if check_cancelled is not None:
                check_cancelled()
            while expected_index not in ordered:
                item = self._next_frame(
                    run.output,
                    run.futures,
                    run.cancelled,
                    check_cancelled=check_cancelled,
                )
                if item.index < expected_index or item.index in ordered:
                    raise RuntimeError("Parallel editable media capture returned a duplicate frame")
                ordered[item.index] = item
            on_frame(ordered.pop(expected_index).payload)
            if on_progress is not None:
                on_progress(completed_count)

    def _record_metrics(
        self,
        run: _CaptureRun,
        frame_count: int,
        workers: list[_WorkerMetrics],
        *,
        fallback_reason: str | None,
    ) -> WebCaptureMetrics:
        frame_times = [value for worker in workers for value in worker.frame_times_ms]
        metrics = WebCaptureMetrics(
            worker_count=run.sizing.workers,
            frame_count=frame_count,
            captured_frames=sum(item.captured_frames for item in workers),
            fast_capture_workers=sum(item.fast_capture for item in workers),
            capture_backend=("drawelement" if all(item.fast_capture for item in workers) else "screenshot"),
            capture_backend_reason="; ".join(dict.fromkeys(item.capture_backend_reason for item in workers)),
            fallback_reason=fallback_reason,
            sizing=run.sizing,
            seek_seconds=sum(item.seek_seconds for item in workers),
            capture_seconds=sum(item.capture_seconds for item in workers),
            queue_wait_seconds=sum(item.queue_wait_seconds for item in workers),
            frame_time_p50_ms=_percentile(frame_times, 0.50),
            frame_time_p95_ms=_percentile(frame_times, 0.95),
            elapsed_seconds=time.perf_counter() - run.started,
            work_steal_count=run.scheduler.work_steal_count,
            worker_frame_counts=(run.scheduler.worker_frame_counts),
            retry_count=sum(item.retry_count for item in workers),
            page_replacement_count=sum(item.page_replacement_count for item in workers),
            browser_replacement_count=sum(item.browser_replacement_count for item in workers),
            readiness_wait_seconds=sum(item.readiness_wait_seconds for item in workers),
            timeout_labels=tuple(sorted({label for item in workers for label in item.timeout_labels})),
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

    @contextmanager
    def _render_lock(
        self,
        check_cancelled: Callable[[], None] | None,
    ) -> Iterator[None]:
        while True:
            if check_cancelled is not None:
                check_cancelled()
            if self._lock.acquire(timeout=0.1):
                break
        try:
            if check_cancelled is not None:
                check_cancelled()
            yield
        finally:
            self._lock.release()

    @staticmethod
    def _await_worker_metrics(
        futures: list[Future[_WorkerMetrics]],
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> list[_WorkerMetrics]:
        pending = set(futures)
        while pending:
            if check_cancelled is not None:
                check_cancelled()
            completed, pending = wait(pending, timeout=0.1)
            for future in completed:
                future.result()
        return [future.result() for future in futures]

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


_ENGINE_POOL = IdleResourcePool[WebCaptureEngine]()
_ENGINE_IDLE_SECONDS = 150.0


def get_web_capture_engine(executable: Path) -> WebCaptureEngine:
    resolved = executable.resolve()
    return _ENGINE_POOL.acquire(resolved, lambda: WebCaptureEngine(resolved))


def release_web_capture_engine(executable: Path, engine: WebCaptureEngine) -> None:
    """Retire idle Chromium workers after a short reuse window."""
    resolved = executable.resolve()
    _ENGINE_POOL.release(resolved, engine, idle_seconds=_ENGINE_IDLE_SECONDS)


def web_capture_diagnostics(executable: Path) -> WebCaptureDiagnostics:
    engine = _ENGINE_POOL.peek(executable.resolve())
    return (
        WebCaptureDiagnostics(0, 0, 0, None, None)
        if engine is None
        else engine.diagnostics()
    )


@atexit.register
def shutdown_web_capture_engines() -> None:
    _ENGINE_POOL.close_all()
