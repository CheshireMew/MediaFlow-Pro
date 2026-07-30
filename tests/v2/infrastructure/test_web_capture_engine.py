from __future__ import annotations

import struct
from pathlib import Path

import pytest

import mediaflow.infrastructure.web_capture_engine as web_capture_module
from mediaflow.infrastructure.chromium_runtime import find_chromium_executable
from mediaflow.infrastructure.web_browser import (
    WebPackagePreviewServer,
    verify_non_monotonic_seek_pixels,
)
from mediaflow.infrastructure.web_capture_engine import (
    FastCaptureFallbackRequired,
    WebCaptureEngine,
    _compare_fast_capture,
    _fast_capture_sample_indices,
    _resolve_worker_count,
    _validate_png,
)


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


def _write_capture_page(path: Path, *, fail_fast_capture: bool) -> None:
    failure_script = (
        """
const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function (...arguments_) {
  if (this.id === "__mediaflow_capture_canvas") {
    window.__fastCaptureCalls = (window.__fastCaptureCalls || 0) + 1;
    if (window.__fastCaptureCalls === 8) {
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
    assert {frame % 4 for frame in samples} == {0, 1, 2, 3}
    assert samples[0] == 0
    assert samples[-1] >= round(609 * 0.94)


def test_worker_sizing_reports_memory_as_the_real_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_capture_module,
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


def test_png_validation_rejects_wrong_dimensions_and_non_png_payloads() -> None:
    _validate_png(_png_header(1920, 1080), 1920, 1080)

    with pytest.raises(RuntimeError, match="wrong frame size"):
        _validate_png(_png_header(1280, 720), 1920, 1080)
    with pytest.raises(RuntimeError, match="invalid PNG"):
        _validate_png(b"not a png", 1920, 1080)


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
def test_real_draw_element_failure_requires_clean_screenshot_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_capture_page(tmp_path / "index.html", fail_fast_capture=True)
    monkeypatch.setenv("MEDIAFLOW_WEB_FAST_CAPTURE", "1")
    engine = WebCaptureEngine(find_chromium_executable())
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
            assert first_attempt

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
