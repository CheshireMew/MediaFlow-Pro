from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

_FAST_CAPTURE_MIN_PSNR_DB = 36.0
_FAST_CAPTURE_MIN_BLURRED_PSNR_DB = 43.0
_FAST_CAPTURE_MAX_MEAN_ABSOLUTE_ERROR = 0.75
_FAST_CAPTURE_MAX_BLURRED_CHANNEL_ERROR = 48

WebCaptureMode = Literal["auto", "screenshot"]
WebWorkerSizingBound = Literal["worker_limit", "work", "memory", "pixels"]


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
    work_steal_count: int
    worker_frame_counts: tuple[int, ...]
    retry_count: int
    page_replacement_count: int
    browser_replacement_count: int
    readiness_wait_seconds: float
    timeout_labels: tuple[str, ...]


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
    retry_count: int
    page_replacement_count: int
    browser_replacement_count: int
    readiness_wait_seconds: float
    timeout_labels: tuple[str, ...]


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


class _FastFrameCaptureError(RuntimeError):
    pass


class WebFrameCaptureError(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(
            f"frame={detail.get('frame_index', '?')}, "
            f"label={detail.get('label') or 'unknown'}, "
            f"code={detail.get('code') or 'frame_task_failed'}: "
            f"{detail.get('message') or 'Editable media frame failed'}"
        )
        self.code = str(detail.get("code") or "frame_task_failed")
        self.label = str(detail.get("label") or "")
        self.retryable = detail.get("retryable") is True
        self.seconds = float(detail.get("seconds") or 0.0)
        self.generation = int(detail.get("generation") or 0)


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
            and self.mean_absolute_error <= _FAST_CAPTURE_MAX_MEAN_ABSOLUTE_ERROR
            and self.blurred_channel_error <= _FAST_CAPTURE_MAX_BLURRED_CHANNEL_ERROR
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
