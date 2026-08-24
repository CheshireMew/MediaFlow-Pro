# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.environment import test_run_root
from scripts.run_artifacts import verification_workspace_root

FIXTURE = ROOT / "tests" / "fixtures" / "editable-media-v6"
DEFAULT_RUN_ROOT = test_run_root() / "web-render-performance"
MIN_PARALLEL_SPEEDUP = 1.35
MIN_FRAME_PSNR_DB = 60.0
MIN_SLOW_FRAME_SCHEDULER_IMPROVEMENT = 0.20
MAX_BALANCED_BASELINE_REGRESSION = 0.10
MAX_FRAME_TIME_P95_MS = 110.0


class RenderResult(TypedDict):
    cache: str
    seconds: float
    capture_elapsed_seconds: float
    non_capture_seconds: float
    throughput_fps: float
    worker_count: int
    captured_frames: int
    fast_capture_workers: int
    capture_backend: str
    capture_backend_reason: str
    fallback_reason: str | None
    worker_bound: str
    available_memory_bytes: int
    estimated_worker_bytes: int
    seek_seconds: float
    capture_seconds: float
    queue_wait_seconds: float
    frame_time_p50_ms: float
    frame_time_p95_ms: float
    browser_launches: int
    failed_render_count: int
    work_steal_count: int
    worker_frame_counts: list[int]
    retry_count: int
    page_replacement_count: int
    browser_replacement_count: int
    readiness_wait_seconds: float
    timeout_labels: list[str]


def web_render_requirements_met(
    *,
    frame_count: int,
    serial_seconds: float,
    parallel_seconds: float,
    serial_frame_count: int,
    parallel_frame_count: int,
    identical_frames: int,
    minimum_frame_psnr_db: float,
    parallel_workers: int,
    parallel_fast_capture_workers: int,
    parallel_capture_backend: str,
    serial_frame_time_p95_ms: float,
    parallel_frame_time_p95_ms: float,
    expected_parallel_workers: int,
    slow_modulo_seconds: float,
    slow_dynamic_seconds: float,
) -> bool:
    return (
        frame_count > 0
        and serial_seconds > 0
        and parallel_seconds > 0
        and serial_seconds / parallel_seconds >= MIN_PARALLEL_SPEEDUP
        and serial_frame_count == frame_count
        and parallel_frame_count == frame_count
        and identical_frames == frame_count
        and minimum_frame_psnr_db >= MIN_FRAME_PSNR_DB
        and parallel_workers > 1
        and parallel_workers == expected_parallel_workers
        and parallel_fast_capture_workers == parallel_workers
        and parallel_capture_backend == "drawelement"
        and parallel_frame_time_p95_ms <= MAX_FRAME_TIME_P95_MS
        and parallel_frame_time_p95_ms <= serial_frame_time_p95_ms
        and slow_modulo_seconds > 0
        and slow_dynamic_seconds > 0
        and 1 - slow_dynamic_seconds / slow_modulo_seconds >= MIN_SLOW_FRAME_SCHEDULER_IMPROVEMENT
    )


def _slow_frame_scheduler_benchmark(
    *,
    frame_count: int = 96,
    worker_count: int = 4,
) -> dict[str, float | int]:
    """Compare the removed modulo assignment with the production scheduler."""

    from mediaflow.infrastructure.web_capture_scheduler import _FrameScheduler

    durations = [0.008 if frame % worker_count == 0 else 0.0005 for frame in range(frame_count)]

    def run_static() -> float:
        barrier = threading.Barrier(worker_count)

        def worker(index: int) -> None:
            barrier.wait()
            for frame in range(index, frame_count, worker_count):
                time.sleep(durations[frame])

        started = time.perf_counter()
        threads = [threading.Thread(target=worker, args=(index,)) for index in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return time.perf_counter() - started

    def run_dynamic() -> tuple[float, int]:
        scheduler = _FrameScheduler(frame_count, worker_count)
        barrier = threading.Barrier(worker_count)

        def worker(index: int) -> None:
            barrier.wait()
            while lease := scheduler.lease(index):
                time.sleep(durations[lease.index])
                scheduler.complete(index, lease.index)

        started = time.perf_counter()
        threads = [threading.Thread(target=worker, args=(index,)) for index in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return time.perf_counter() - started, scheduler.work_steal_count

    modulo_seconds = run_static()
    dynamic_seconds, steals = run_dynamic()
    return {
        "modulo_seconds": modulo_seconds,
        "dynamic_seconds": dynamic_seconds,
        "improvement": 1 - dynamic_seconds / modulo_seconds,
        "work_steal_count": steals,
    }


def _render_case(run_dir: Path, workers: int, frame_count: int) -> RenderResult:
    workspace = verification_workspace_root(run_dir)
    os.environ["MEDIAFLOW_WEB_WORKERS"] = str(workers)
    os.environ["MEDIAFLOW_WEB_FAST_CAPTURE"] = "1"
    os.environ["MEDIAFLOW_SERVICE_SETTINGS_PATH"] = str(
        workspace / f"settings-{workers}" / "service-settings.json"
    )
    os.environ["MEDIAFLOW_MEDIA_ROOT"] = str(workspace / f"media-{workers}")
    os.environ["MEDIAFLOW_PROJECT_ROOT"] = str(workspace / f"projects-{workers}")

    from mediaflow.application.timeline_editor import TimelineEditor
    from mediaflow.application.web_media_service import WebMediaServices
    from mediaflow.domain.enums import TrackKind
    from mediaflow.infrastructure.editable_media_contract import editable_media_contract
    from mediaflow.infrastructure.project_repository import ProjectRepository
    from mediaflow.infrastructure.runtime_context import RuntimeContext
    from mediaflow.infrastructure.structured_file_reader import LocalStructuredFileReader
    from mediaflow.infrastructure.web_browser import BrowserWebPackageValidator
    from mediaflow.infrastructure.web_capture_engine import web_capture_diagnostics
    from mediaflow.infrastructure.web_package_storage import LocalWebPackageStorage
    from mediaflow.infrastructure.web_render_service import WebRenderService

    runtime = RuntimeContext.discover()
    paths = runtime.paths
    project_dir = workspace / f"workers-{workers}"
    with ProjectRepository.create(
        project_dir,
        f"Web render performance workers {workers}",
    ) as repository:
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        services = WebMediaServices(
            repository,
            lambda sequence_id: editor,
            BrowserWebPackageValidator(paths.chromium, editable_media_contract()),
            LocalStructuredFileReader(),
            LocalWebPackageStorage(),
            editable_media_contract(),
        )
        asset = services.packages.import_package(FIXTURE)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=frame_count,
        )
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        started = time.perf_counter()
        cache = WebRenderService(repository, paths).render_clip(
            timeline,
            clip.id,
        )
        elapsed = time.perf_counter() - started
        assert paths.chromium is not None
        diagnostics = web_capture_diagnostics(paths.chromium)
        metrics = diagnostics.last_metrics
        if metrics is None:
            raise RuntimeError("Web capture engine did not publish render metrics")
        return {
            "cache": str(cache),
            "seconds": elapsed,
            "capture_elapsed_seconds": metrics.elapsed_seconds,
            "non_capture_seconds": max(0.0, elapsed - metrics.elapsed_seconds),
            "throughput_fps": frame_count / elapsed,
            "worker_count": metrics.worker_count,
            "captured_frames": metrics.captured_frames,
            "fast_capture_workers": metrics.fast_capture_workers,
            "capture_backend": metrics.capture_backend,
            "capture_backend_reason": metrics.capture_backend_reason,
            "fallback_reason": metrics.fallback_reason,
            "worker_bound": metrics.sizing.bound_by,
            "available_memory_bytes": metrics.sizing.available_memory_bytes,
            "estimated_worker_bytes": metrics.sizing.estimated_worker_bytes,
            "seek_seconds": metrics.seek_seconds,
            "capture_seconds": metrics.capture_seconds,
            "queue_wait_seconds": metrics.queue_wait_seconds,
            "frame_time_p50_ms": metrics.frame_time_p50_ms,
            "frame_time_p95_ms": metrics.frame_time_p95_ms,
            "browser_launches": diagnostics.browser_launches,
            "failed_render_count": diagnostics.failed_render_count,
            "work_steal_count": metrics.work_steal_count,
            "worker_frame_counts": list(metrics.worker_frame_counts),
            "retry_count": metrics.retry_count,
            "page_replacement_count": metrics.page_replacement_count,
            "browser_replacement_count": metrics.browser_replacement_count,
            "readiness_wait_seconds": metrics.readiness_wait_seconds,
            "timeout_labels": list(metrics.timeout_labels),
        }


def _frame_hashes(ffmpeg: Path, video: Path) -> list[str]:
    result = subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-i",
            str(video),
            "-f",
            "framemd5",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        line.rsplit(",", 1)[-1].strip()
        for line in result.stdout.splitlines()
        if line and not line.startswith("#")
    ]


def _minimum_frame_psnr(ffmpeg: Path, left: Path, right: Path) -> float:
    result = subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-i",
            str(left),
            "-i",
            str(right),
            "-lavfi",
            "psnr=stats_file=-",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    values = [
        float(match.group(1)) for match in re.finditer(r"\bpsnr_avg:([0-9]+(?:\.[0-9]+)?)", result.stdout)
    ]
    return min(values, default=float("inf"))


def _child_result(
    script: Path,
    run_dir: Path,
    workers: int,
    frame_count: int,
) -> RenderResult:
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--child",
            "--run-dir",
            str(run_dir),
            "--workers",
            str(workers),
            "--frames",
            str(frame_count),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    (run_dir / f"workers-{workers}.stdout.log").write_text(
        result.stdout,
        encoding="utf-8",
    )
    (run_dir / f"workers-{workers}.stderr.log").write_text(
        result.stderr,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Web render worker {workers} failed; see {run_dir / f'workers-{workers}.stderr.log'}"
        )
    marker = "MEDIAFLOW_WEB_RENDER_RESULT="
    line = next(
        (item for item in reversed(result.stdout.splitlines()) if item.startswith(marker)),
        None,
    )
    if line is None:
        raise RuntimeError(f"Web render worker {workers} returned no result")
    payload = json.loads(line[len(marker) :])
    if not isinstance(payload, dict):
        raise RuntimeError(f"Web render worker {workers} returned an invalid result")
    if (
        not isinstance(payload.get("cache"), str)
        or not isinstance(payload.get("seconds"), (int, float))
        or not isinstance(payload.get("capture_elapsed_seconds"), (int, float))
        or not isinstance(payload.get("non_capture_seconds"), (int, float))
        or not isinstance(payload.get("throughput_fps"), (int, float))
        or not isinstance(payload.get("worker_count"), int)
        or not isinstance(payload.get("captured_frames"), int)
        or not isinstance(payload.get("fast_capture_workers"), int)
        or payload.get("capture_backend") not in {"drawelement", "screenshot"}
        or not isinstance(payload.get("capture_backend_reason"), str)
        or not payload["capture_backend_reason"]
        or (
            payload.get("fallback_reason") is not None and not isinstance(payload.get("fallback_reason"), str)
        )
        or payload.get("worker_bound") not in {"worker_limit", "work", "memory", "pixels"}
        or not isinstance(payload.get("available_memory_bytes"), int)
        or not isinstance(payload.get("estimated_worker_bytes"), int)
        or not isinstance(payload.get("seek_seconds"), (int, float))
        or not isinstance(payload.get("capture_seconds"), (int, float))
        or not isinstance(payload.get("queue_wait_seconds"), (int, float))
        or not isinstance(payload.get("frame_time_p50_ms"), (int, float))
        or not isinstance(payload.get("frame_time_p95_ms"), (int, float))
        or not isinstance(payload.get("browser_launches"), int)
        or not isinstance(payload.get("failed_render_count"), int)
        or not isinstance(payload.get("work_steal_count"), int)
        or not isinstance(payload.get("worker_frame_counts"), list)
        or not all(isinstance(value, int) for value in payload["worker_frame_counts"])
        or not isinstance(payload.get("retry_count"), int)
        or not isinstance(payload.get("page_replacement_count"), int)
        or not isinstance(payload.get("browser_replacement_count"), int)
        or not isinstance(payload.get("readiness_wait_seconds"), (int, float))
        or not isinstance(payload.get("timeout_labels"), list)
        or not all(isinstance(value, str) for value in payload["timeout_labels"])
    ):
        raise RuntimeError(f"Web render worker {workers} returned incomplete metrics")
    return cast(RenderResult, payload)


def _new_run_dir(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    path = root / f"r-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    return path


def _balanced_baseline_comparison(
    baseline_path: Path,
    *,
    frame_count: int,
    current: RenderResult,
) -> dict[str, float | str | bool]:
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("frame_count") != frame_count:
        raise ValueError("Web render baseline must use the same frame count")
    baseline = payload.get("parallel")
    if not isinstance(baseline, dict):
        raise ValueError("Web render baseline has no parallel result")
    if baseline.get("capture_backend") != current["capture_backend"]:
        raise ValueError("Web render baseline must use the same capture backend")
    baseline_seconds = float(baseline["seconds"])
    baseline_p95_ms = float(baseline["frame_time_p95_ms"])
    total_ratio = float(current["seconds"]) / baseline_seconds
    p95_ratio = float(current["frame_time_p95_ms"]) / baseline_p95_ms
    return {
        "path": str(baseline_path),
        "baseline_seconds": baseline_seconds,
        "current_seconds": float(current["seconds"]),
        "total_ratio": total_ratio,
        "total_improvement": 1 - total_ratio,
        "baseline_worker_count": int(baseline["worker_count"]),
        "current_worker_count": int(current["worker_count"]),
        "baseline_p95_ms": baseline_p95_ms,
        "current_p95_ms": float(current["frame_time_p95_ms"]),
        "p95_ratio": p95_ratio,
        "maximum_regression": MAX_BALANCED_BASELINE_REGRESSION,
        "passed": (
            total_ratio <= 1 + MAX_BALANCED_BASELINE_REGRESSION
            and p95_ratio <= 1 + MAX_BALANCED_BASELINE_REGRESSION
        ),
    }


def verify(
    frame_count: int,
    run_root: Path,
    *,
    baseline_report: Path | None = None,
) -> int:
    if frame_count < 151:
        raise ValueError("Web render verification needs at least 151 frames")
    from mediaflow.infrastructure.runtime_context import RuntimeContext
    from mediaflow.infrastructure.web_capture_scheduler import _MIN_FRAMES_PER_WORKER

    run_dir = _new_run_dir(run_root)
    script = Path(__file__).resolve()
    serial = _child_result(script, run_dir, 1, frame_count)
    parallel = _child_result(script, run_dir, 4, frame_count)
    ffmpeg = RuntimeContext.discover().paths.ffmpeg
    serial_cache = Path(serial["cache"])
    parallel_cache = Path(parallel["cache"])
    serial_hashes = _frame_hashes(ffmpeg, serial_cache)
    parallel_hashes = _frame_hashes(ffmpeg, parallel_cache)
    identical_frames = sum(left == right for left, right in zip(serial_hashes, parallel_hashes, strict=False))
    minimum_frame_psnr_db = _minimum_frame_psnr(
        ffmpeg,
        serial_cache,
        parallel_cache,
    )
    slow_scheduler = _slow_frame_scheduler_benchmark()
    expected_parallel_workers = min(
        4,
        max(1, math.ceil(frame_count / _MIN_FRAMES_PER_WORKER)),
    )
    baseline = (
        _balanced_baseline_comparison(
            baseline_report.resolve(strict=True),
            frame_count=frame_count,
            current=parallel,
        )
        if baseline_report is not None
        else None
    )
    passed = web_render_requirements_met(
        frame_count=frame_count,
        serial_seconds=float(serial["seconds"]),
        parallel_seconds=float(parallel["seconds"]),
        serial_frame_count=len(serial_hashes),
        parallel_frame_count=len(parallel_hashes),
        identical_frames=identical_frames,
        minimum_frame_psnr_db=minimum_frame_psnr_db,
        parallel_workers=int(parallel["worker_count"]),
        parallel_fast_capture_workers=int(parallel["fast_capture_workers"]),
        parallel_capture_backend=parallel["capture_backend"],
        serial_frame_time_p95_ms=float(serial["frame_time_p95_ms"]),
        parallel_frame_time_p95_ms=float(parallel["frame_time_p95_ms"]),
        expected_parallel_workers=expected_parallel_workers,
        slow_modulo_seconds=float(slow_scheduler["modulo_seconds"]),
        slow_dynamic_seconds=float(slow_scheduler["dynamic_seconds"]),
    ) and (baseline is None or baseline["passed"] is True)
    report = {
        "schema": "mediaflow-web-render-performance/v1",
        "status": "passed" if passed else "failed",
        "frame_count": frame_count,
        "serial_frame_count": len(serial_hashes),
        "parallel_frame_count": len(parallel_hashes),
        "identical_frames": identical_frames,
        "minimum_frame_psnr_db": minimum_frame_psnr_db,
        "serial": serial,
        "parallel": parallel,
        "speedup": float(serial["seconds"]) / float(parallel["seconds"]),
        "minimum_speedup": MIN_PARALLEL_SPEEDUP,
        "expected_parallel_workers": expected_parallel_workers,
        "maximum_frame_time_p95_ms": MAX_FRAME_TIME_P95_MS,
        "required_minimum_frame_psnr_db": MIN_FRAME_PSNR_DB,
        "slow_frame_scheduler": slow_scheduler,
        "minimum_slow_frame_improvement": MIN_SLOW_FRAME_SCHEDULER_IMPROVEMENT,
        "balanced_baseline": baseline,
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"REPORT={run_dir / 'report.json'}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=180)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--workers", type=int)
    arguments = parser.parse_args(argv)
    if arguments.child:
        if arguments.run_dir is None or arguments.workers is None:
            parser.error("--child requires --run-dir and --workers")
        result = _render_case(
            arguments.run_dir.resolve(),
            arguments.workers,
            arguments.frames,
        )
        print("MEDIAFLOW_WEB_RENDER_RESULT=" + json.dumps(result))
        return 0
    return verify(
        arguments.frames,
        arguments.run_root.resolve(),
        baseline_report=arguments.baseline_report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
