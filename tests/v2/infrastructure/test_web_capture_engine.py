from __future__ import annotations

import queue
import struct
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import pytest

import mediaflow.infrastructure.web_capture_engine as web_capture_module
import mediaflow.infrastructure.web_capture_prewarm as web_capture_prewarm_module
import mediaflow.infrastructure.web_capture_scheduler as web_capture_scheduler_module
from mediaflow.application.task_service import TaskStopped
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.web_manifest import EditableMediaManifest
from mediaflow.domain.web_state import (
    WebClipState,
    web_runtime_state,
)
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.web_browser import (
    WebPackagePreviewServer,
    verify_non_monotonic_seek_pixels,
)
from mediaflow.infrastructure.web_capture_engine import (
    FastCaptureFallbackRequired,
    WebCaptureEngine,
    _BrowserPoolGeneration,
    _compare_fast_capture,
    _fast_capture_sample_indices,
    _FrameScheduler,
    _resolve_worker_count,
    _validate_png,
)

REACT_REFERENCE = Path("tests/fixtures/editable-media-v6-react-reference")


class _SeekablePage:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.capture_count = 0

    def evaluate(self, _script: str, seconds: float | None = None) -> None:
        if seconds is not None:
            self.seconds = seconds
        elif "__hf.seek(0)" in _script:
            self.seconds = 0.0

    def deterministic_capture(self) -> bytes:
        return f"frame:{self.seconds:.6f}".encode()

    def order_dependent_capture(self) -> bytes:
        self.capture_count += 1
        return f"frame:{self.seconds:.6f}:capture:{self.capture_count}".encode()


def _png_header(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", width, height)


def test_shared_capture_engine_retires_after_idle_reuse_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_capture_module.shutdown_web_capture_engines()
    closed = threading.Event()
    created: list[object] = []

    class FakeEngine:
        def __init__(self, executable: Path):
            self.executable = executable
            created.append(self)

        def diagnostics(self):
            return web_capture_module.WebCaptureDiagnostics(
                browser_launches=2,
                render_count=1,
                failed_render_count=0,
                last_metrics=None,
                last_failure=None,
            )

        def close(self) -> None:
            closed.set()

    monkeypatch.setattr(web_capture_module, "WebCaptureEngine", FakeEngine)
    monkeypatch.setattr(web_capture_module, "_ENGINE_IDLE_SECONDS", 0.01)
    executable = tmp_path / "chromium.exe"

    engine = web_capture_module.get_web_capture_engine(executable)
    assert web_capture_module.web_capture_diagnostics(executable).render_count == 1
    web_capture_module.release_web_capture_engine(executable, engine)

    assert closed.wait(timeout=1)
    assert web_capture_module.web_capture_diagnostics(executable).render_count == 0
    assert len(created) == 1


def test_shared_capture_engine_waits_for_every_active_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_capture_module.shutdown_web_capture_engines()
    closed = threading.Event()

    class FakeEngine:
        def __init__(self, _executable: Path):
            pass

        def close(self) -> None:
            closed.set()

    monkeypatch.setattr(web_capture_module, "WebCaptureEngine", FakeEngine)
    monkeypatch.setattr(web_capture_module, "_ENGINE_IDLE_SECONDS", 0.01)
    executable = tmp_path / "chromium.exe"
    first = web_capture_module.get_web_capture_engine(executable)
    second = web_capture_module.get_web_capture_engine(executable)
    assert first is second

    web_capture_module.release_web_capture_engine(executable, first)
    assert not closed.wait(timeout=0.05)
    web_capture_module.release_web_capture_engine(executable, second)
    assert closed.wait(timeout=1)


def test_background_capture_prewarm_is_deduplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_capture_module.shutdown_web_capture_engines()
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    class FakeEngine:
        def __init__(self, _executable: Path):
            pass

        def prewarm(self, *, worker_count: int) -> None:
            calls.append(worker_count)
            entered.set()
            assert release.wait(timeout=1)

        def close(self) -> None:
            pass

    monkeypatch.setattr(web_capture_module, "WebCaptureEngine", FakeEngine)
    monkeypatch.setattr(web_capture_module, "_ENGINE_IDLE_SECONDS", 0.01)
    executable = tmp_path / "chromium.exe"

    assert web_capture_prewarm_module.prewarm_web_capture_engine(executable)
    assert entered.wait(timeout=1)
    assert not web_capture_prewarm_module.prewarm_web_capture_engine(executable)
    release.set()
    deadline = time.monotonic() + 1
    while executable.resolve() in web_capture_prewarm_module._PREWARMING:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert calls == [1]


def _write_capture_page(path: Path, *, fail_fast_capture: bool) -> None:
    failure_script = (
        """
const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function (...arguments_) {
  if (this.id === "__mediaflow_capture_canvas") {
    if (window.__mediaflowFastCapturePhase === "production") {
      window.__fastCaptureProductionCalls =
        (window.__fastCaptureProductionCalls || 0) + 1;
    }
    if (window.__fastCaptureProductionCalls === 1) {
      throw new Error("intentional drawElement production failure");
    }
  }
  return originalToDataURL.apply(this, arguments_);
};
"""
        if fail_fast_capture
        else ""
    )
    path.write_text(
        f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      background: transparent;
    }}
    [data-composition-id] {{
      position: relative;
      width: 100vw;
      height: 100vh;
      overflow: hidden;
      background: rgb(20, 32, 48);
    }}
    #moving {{
      position: absolute;
      inset: 12% auto 12% 8%;
      width: 36%;
      background: rgb(245, 110, 40);
    }}
  </style>
</head>
<body>
  <main data-composition-id="capture-test"><div id="moving"></div></main>
  <script>
    let state = {{}};
    const moving = document.querySelector("#moving");
    window.editableMedia = {{
      ready: Promise.resolve(),
      setState(value) {{ state = structuredClone(value); }},
      getState() {{ return structuredClone(state); }},
    }};
    window.__hf = {{
      duration: 2,
      async seek(seconds) {{
        const progress = Math.max(0, Math.min(1, seconds / 2));
        moving.style.transform = `translateX(${{Math.round(progress * 420)}}px)`;
        return {{seconds, generation: Math.round(seconds * 1000) + 1, wait_ms: 0, tasks: []}};
      }},
    }};
    {failure_script}
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def test_determinism_probe_accepts_random_access_pixels_and_returns_to_zero() -> None:
    page = _SeekablePage()

    verify_non_monotonic_seek_pixels(
        page,
        20.0,
        page.deterministic_capture,
    )

    assert page.seconds == 0.0


def test_determinism_probe_rejects_call_order_side_effects() -> None:
    page = _SeekablePage()

    with pytest.raises(ValueError, match="non-monotonic frame seeks"):
        verify_non_monotonic_seek_pixels(
            page,
            20.0,
            page.order_dependent_capture,
        )

    assert page.seconds == 0.0


def test_fast_capture_samples_cover_every_worker_and_late_frames() -> None:
    samples = _fast_capture_sample_indices(frame_count=610, worker_count=4)

    assert len(samples) == 10
    assert samples[0] == 0
    assert samples[-1] >= round(609 * 0.94)
    for worker_index in range(4):
        start = 610 * worker_index // 4
        end = 610 * (worker_index + 1) // 4
        assert len([frame for frame in samples if start <= frame < end]) >= 2


def test_contiguous_scheduler_steals_tail_work_and_retries_the_same_frame() -> None:
    scheduler = _FrameScheduler(frame_count=12, worker_count=3)
    for expected in range(4):
        lease = scheduler.lease(0)
        assert lease is not None and lease.index == expected
        scheduler.complete(0, lease.index)

    stolen = scheduler.lease(0)
    assert stolen is not None
    assert stolen.index in {6, 7}
    scheduler.return_frame(0, stolen.index)
    retried = scheduler.lease(0)
    assert retried is not None
    assert retried.index == stolen.index
    assert retried.attempt == 2
    scheduler.complete(0, retried.index)
    assert scheduler.work_steal_count == 1
    assert scheduler.worker_frame_counts[0] == 5


def test_contiguous_scheduler_preserves_absolute_start_frame() -> None:
    scheduler = _FrameScheduler(frame_count=3, worker_count=1, start_frame=20)

    first = scheduler.lease(0)
    assert first is not None and (first.index, first.attempt) == (20, 1)
    scheduler.complete(0, first.index)
    second = scheduler.lease(0)
    assert second is not None and (second.index, second.attempt) == (21, 1)
    scheduler.return_frame(0, second.index)
    retry = scheduler.lease(0)
    assert retry is not None and (retry.index, retry.attempt) == (21, 2)
    scheduler.complete(0, retry.index)
    third = scheduler.lease(0)
    assert third is not None and (third.index, third.attempt) == (22, 1)
    scheduler.complete(0, third.index)
    assert scheduler.lease(0) is None


def test_browser_pool_generation_is_replaced_once_for_concurrent_failures() -> None:
    generation = _BrowserPoolGeneration()

    assert generation.current == 0
    assert generation.invalidate(0) == 1
    assert generation.invalidate(0) == 1
    assert generation.invalidate(1) == 2


def test_worker_sizing_reports_memory_as_the_real_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_capture_scheduler_module,
        "available_physical_memory_bytes",
        lambda: 512 * 1024**2,
    )

    sizing = _resolve_worker_count(
        frame_count=610,
        width=1080,
        height=1920,
        limit=4,
    )

    assert sizing.workers == 1
    assert sizing.bound_by == "memory"
    assert sizing.memory_limit == 1
    assert sizing.estimated_worker_bytes > 256 * 1024**2


def test_worker_sizing_keeps_the_cold_180_frame_case_balanced_at_two_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_capture_scheduler_module,
        "available_physical_memory_bytes",
        lambda: 16 * 1024**3,
    )

    sizing = _resolve_worker_count(
        frame_count=180,
        width=1080,
        height=1080,
        limit=4,
    )

    assert sizing.workers == 2
    assert sizing.bound_by == "work"
    assert sizing.work_limit == 2


def test_png_validation_rejects_wrong_dimensions_and_non_png_payloads() -> None:
    _validate_png(_png_header(1920, 1080), 1920, 1080)

    with pytest.raises(RuntimeError, match="wrong frame size"):
        _validate_png(_png_header(1280, 720), 1920, 1080)
    with pytest.raises(RuntimeError, match="invalid PNG"):
        _validate_png(b"not a png", 1920, 1080)


def test_frame_wait_observes_task_cancellation_before_first_browser_frame() -> None:
    started = time.monotonic()

    def cancelled() -> None:
        raise TaskStopped(TaskStatus.PAUSED)

    with pytest.raises(TaskStopped):
        WebCaptureEngine._next_frame(
            queue.Queue(),
            [Future()],
            threading.Event(),
            check_cancelled=cancelled,
        )

    assert time.monotonic() - started < 0.1


def test_fast_capture_comparison_accepts_antialiasing_but_rejects_missing_content() -> None:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    reference = numpy.full((256, 256, 4), 255, dtype=numpy.uint8)
    candidate = reference.copy()
    candidate[40:216:4, 32:224:4, :3] = 232
    missing = reference.copy()
    missing[64:192, 64:192, :3] = 0

    def png(image) -> bytes:
        encoded, payload = cv2.imencode(".png", image)
        assert encoded
        return payload.tobytes()

    antialiasing = _compare_fast_capture(png(reference), png(candidate))
    content_loss = _compare_fast_capture(png(missing), png(reference))

    assert antialiasing.accepted
    assert antialiasing.alpha_equal
    assert not content_loss.accepted


@pytest.mark.integration
def test_real_react_retryable_frame_replaces_page_and_preserves_order() -> None:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    manifest = EditableMediaManifest.model_validate_json(
        (REACT_REFERENCE / "editable-media.json").read_text(encoding="utf-8")
    )
    runtime_state = web_runtime_state(
        WebClipState(clip_id="react-retry-integration"),
        manifest,
    )
    engine = WebCaptureEngine(RuntimeContext.discover().paths.chromium)
    frames: list[bytes] = []
    try:
        with WebPackagePreviewServer(REACT_REFERENCE) as preview:
            metrics = engine.render_frames(
                url=preview.url_for(
                    manifest.entry,
                    query=(
                        "capture=1&variant=landscape&scene=react-orbit&frame_delay_ms=15&fail_once_frame=2"
                    ),
                ),
                allowed_origin=preview.url_for(""),
                width=manifest.default_variant.canvas.width,
                height=manifest.default_variant.canvas.height,
                fps_numerator=manifest.playback.fps,
                fps_denominator=1,
                runtime_state=runtime_state,
                determinism_key="real-react-retry",
                frame_count=8,
                on_frame=frames.append,
                capture_mode="screenshot",
                retry_limit=manifest.frame_readiness.retry_limit,
            )
    finally:
        engine.close()

    assert len(frames) == 8
    progress_pixels = []
    for payload in frames:
        image = cv2.imdecode(numpy.frombuffer(payload, dtype=numpy.uint8), cv2.IMREAD_COLOR)
        assert image is not None
        progress = image[640:648, 74:1206]
        accent = (progress[:, :, 0] > 180) & (progress[:, :, 1] > 130) & (progress[:, :, 2] > 90)
        progress_pixels.append(int(accent.sum()))
    assert progress_pixels == sorted(progress_pixels)
    assert len(set(progress_pixels)) == len(progress_pixels)
    assert metrics.retry_count == 1
    assert metrics.page_replacement_count == 1
    assert metrics.browser_replacement_count == 0
    assert metrics.readiness_wait_seconds >= 0.12
    assert metrics.worker_frame_counts == (8,)


@pytest.mark.integration
def test_real_browser_process_loss_replaces_the_pool_and_retries_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_capture_page(tmp_path / "index.html", fail_fast_capture=False)
    monkeypatch.setenv("MEDIAFLOW_WEB_WORKERS", "1")
    original_capture = web_capture_module._BrowserWorker._capture_screenshot
    capture_calls = 0
    browser_closed = threading.Event()

    def close_browser_once(cdp, width: int, height: int) -> bytes:
        nonlocal capture_calls
        capture_calls += 1
        # The deterministic random-seek proof uses 15 screenshots. Kill the
        # real Chromium process on the first production frame, not the probe.
        if capture_calls == 16:
            browser_closed.set()
            cdp.send("Browser.close")
        return original_capture(cdp, width, height)

    monkeypatch.setattr(
        web_capture_module._BrowserWorker,
        "_capture_screenshot",
        staticmethod(close_browser_once),
    )
    engine = WebCaptureEngine(RuntimeContext.discover().paths.chromium)
    frames: list[bytes] = []
    try:
        with WebPackagePreviewServer(tmp_path) as preview:
            metrics = engine.render_frames(
                url=preview.url_for("index.html"),
                allowed_origin=preview.url_for(""),
                width=320,
                height=180,
                fps_numerator=10,
                fps_denominator=1,
                runtime_state={"revision": 1},
                determinism_key="real-browser-process-replacement",
                frame_count=8,
                on_frame=frames.append,
                capture_mode="screenshot",
                retry_limit=1,
            )
    finally:
        engine.close()

    assert browser_closed.is_set()
    assert len(frames) == 8
    assert metrics.retry_count == 1
    assert metrics.browser_replacement_count == 1
    assert metrics.page_replacement_count == 0
    assert engine.diagnostics().browser_launches == 2


@pytest.mark.integration
def test_real_draw_element_failure_requires_clean_screenshot_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_capture_page(tmp_path / "index.html", fail_fast_capture=True)
    monkeypatch.setenv("MEDIAFLOW_WEB_FAST_CAPTURE", "1")
    engine = WebCaptureEngine(RuntimeContext.discover().paths.chromium)
    try:
        with WebPackagePreviewServer(tmp_path) as preview:
            capture_arguments = {
                "url": preview.url_for("index.html"),
                "allowed_origin": preview.url_for(""),
                "width": 720,
                "height": 1280,
                "fps_numerator": 30,
                "fps_denominator": 1,
                "runtime_state": {"revision": 1},
                "determinism_key": "draw-element-failure",
                "frame_count": 12,
            }
            first_attempt: list[bytes] = []
            with pytest.raises(
                FastCaptureFallbackRequired,
                match="intentional drawElement production failure",
            ) as failure:
                engine.render_frames(
                    **capture_arguments,
                    on_frame=first_attempt.append,
                )
            screenshot_frames: list[bytes] = []
            metrics = engine.render_frames(
                **capture_arguments,
                on_frame=screenshot_frames.append,
                capture_mode="screenshot",
                fallback_reason=str(failure.value),
            )
    finally:
        engine.close()

    assert len(screenshot_frames) == 12
    assert metrics.capture_backend == "screenshot"
    assert "explicitly requires Chrome screenshots" in metrics.capture_backend_reason
    assert metrics.fast_capture_workers == 0
    assert metrics.fallback_reason is not None
    assert metrics.seek_seconds > 0
    assert metrics.capture_seconds > 0
    assert metrics.queue_wait_seconds >= 0
    assert metrics.frame_time_p95_ms >= metrics.frame_time_p50_ms > 0
    diagnostics = engine.diagnostics()
    assert diagnostics.failed_render_count == 1
    assert diagnostics.last_failure is not None
    assert diagnostics.last_failure.error_type == "FastCaptureFallbackRequired"
