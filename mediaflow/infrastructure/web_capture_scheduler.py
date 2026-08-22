from __future__ import annotations

import math
import os
import threading
from collections import deque
from dataclasses import dataclass

from mediaflow.infrastructure.system_resources import available_physical_memory_bytes
from mediaflow.infrastructure.web_capture_models import (
    WebCaptureWorkerSizing,
    WebWorkerSizingBound,
)

_MIN_FRAMES_PER_WORKER = 60
_MIN_PARALLEL_FRAME_COUNT = 120


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


@dataclass(frozen=True, slots=True)
class _FrameLease:
    index: int
    attempt: int


class _FrameScheduler:
    def __init__(self, frame_count: int, worker_count: int, *, start_frame: int = 0) -> None:
        if frame_count <= 0 or worker_count <= 0 or start_frame < 0:
            raise ValueError("Frame scheduler needs positive sizes and a non-negative start")
        self._lock = threading.Lock()
        self._start_frame = start_frame
        self._queues = [
            deque(
                range(
                    start_frame + frame_count * index // worker_count,
                    start_frame + frame_count * (index + 1) // worker_count,
                )
            )
            for index in range(worker_count)
        ]
        self._states = ["pending"] * frame_count
        self._owners: list[int | None] = [None] * frame_count
        self._attempts = [0] * frame_count
        self._worker_counts = [0] * worker_count
        self._work_steal_count = 0

    def lease(self, worker_index: int) -> _FrameLease | None:
        with self._lock:
            own = self._queues[worker_index]
            if not own:
                victim_index, victim = max(
                    enumerate(self._queues),
                    key=lambda item: len(item[1]),
                )
                if victim_index != worker_index and victim:
                    count = max(1, len(victim) // 2)
                    stolen = sorted(victim.pop() for _ in range(count))
                    own.extend(stolen)
                    self._work_steal_count += 1
            if not own:
                return None
            frame_index = own.popleft()
            offset = self._offset(frame_index)
            if self._states[offset] != "pending":
                raise RuntimeError("Editable media frame scheduler state is corrupt")
            self._states[offset] = "leased"
            self._owners[offset] = worker_index
            self._attempts[offset] += 1
            return _FrameLease(frame_index, self._attempts[offset])

    def return_frame(self, worker_index: int, frame_index: int) -> None:
        with self._lock:
            offset = self._offset(frame_index)
            if self._states[offset] != "leased" or self._owners[offset] != worker_index:
                raise RuntimeError("Editable media frame lease cannot be returned")
            self._states[offset] = "pending"
            self._owners[offset] = None
            self._queues[worker_index].appendleft(frame_index)

    def complete(self, worker_index: int, frame_index: int) -> None:
        with self._lock:
            offset = self._offset(frame_index)
            if self._states[offset] != "leased" or self._owners[offset] != worker_index:
                raise RuntimeError("Editable media frame lease cannot be completed")
            self._states[offset] = "completed"
            self._owners[offset] = None
            self._worker_counts[worker_index] += 1

    def _offset(self, frame_index: int) -> int:
        offset = frame_index - self._start_frame
        if not 0 <= offset < len(self._states):
            raise ValueError(f"Frame {frame_index} is outside the scheduler range")
        return offset

    @property
    def work_steal_count(self) -> int:
        with self._lock:
            return self._work_steal_count

    @property
    def worker_frame_counts(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(self._worker_counts)


class _BrowserPoolGeneration:
    """Coordinate an atomic browser-pool replacement across worker threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def current(self) -> int:
        with self._lock:
            return self._generation

    def invalidate(self, observed_generation: int) -> int:
        with self._lock:
            if self._generation == observed_generation:
                self._generation += 1
            return self._generation


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
    by_work = (
        1
        if frame_count < _MIN_PARALLEL_FRAME_COUNT
        else max(1, math.ceil(frame_count / _MIN_FRAMES_PER_WORKER))
    )
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
