# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.environment import test_run_root

FIXTURE = ROOT / "tests" / "fixtures" / "editable-media-v5"
DEFAULT_RUN_ROOT = test_run_root() / "web-render-performance"
MIN_PARALLEL_SPEEDUP = 1.35
MIN_FRAME_PSNR_DB = 60.0


class RenderResult(TypedDict):
    cache: str
    seconds: float
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
        and parallel_fast_capture_workers == parallel_workers
        and parallel_capture_backend == "drawelement"
    )


def _render_case(run_dir: Path, workers: int, frame_count: int) -> RenderResult:
    os.environ["MEDIAFLOW_WEB_WORKERS"] = str(workers)
    os.environ["MEDIAFLOW_WEB_FAST_CAPTURE"] = "1"
    os.environ["MEDIAFLOW_SETTINGS_PATH"] = str(
        run_dir / f"settings-{workers}" / "settings.json"
    )
    os.environ["MEDIAFLOW_MEDIA_ROOT"] = str(run_dir / f"media-{workers}")
    os.environ["MEDIAFLOW_PROJECT_ROOT"] = str(run_dir / f"projects-{workers}")

    from mediaflow.application.timeline_editor import TimelineEditor
    from mediaflow.application.web_media_service import WebMediaServices
    from mediaflow.domain.enums import TrackKind
    from mediaflow.infrastructure.chromium_runtime import find_chromium_executable
    from mediaflow.infrastructure.project_repository import ProjectRepository
    from mediaflow.infrastructure.runtime_paths import RuntimePaths
    from mediaflow.infrastructure.web_browser import BrowserWebPackageValidator
    from mediaflow.infrastructure.web_capture_engine import web_capture_diagnostics
    from mediaflow.infrastructure.web_render_service import WebRenderService

    project_dir = run_dir / f"workers-{workers}"
    with ProjectRepository.create(
        project_dir,
        f"Web render performance workers {workers}",
    ) as repository:
        project = repository.catalog.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        services = WebMediaServices(
            repository,
            lambda sequence_id: editor,
            BrowserWebPackageValidator(),
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
        cache = WebRenderService(repository, RuntimePaths.discover()).render_clip(
            timeline,
            clip.id,
        )
        elapsed = time.perf_counter() - started
        diagnostics = web_capture_diagnostics(find_chromium_executable())
        metrics = diagnostics.last_metrics
        if metrics is None:
            raise RuntimeError("Web capture engine did not publish render metrics")
        return {
            "cache": str(cache),
            "seconds": elapsed,
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
        float(match.group(1))
        for match in re.finditer(r"\bpsnr_avg:([0-9]+(?:\.[0-9]+)?)", result.stdout)
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
        or not isinstance(payload.get("throughput_fps"), (int, float))
        or not isinstance(payload.get("worker_count"), int)
        or not isinstance(payload.get("captured_frames"), int)
        or not isinstance(payload.get("fast_capture_workers"), int)
        or payload.get("capture_backend") not in {"drawelement", "screenshot"}
        or not isinstance(payload.get("capture_backend_reason"), str)
        or not payload["capture_backend_reason"]
        or (
            payload.get("fallback_reason") is not None
            and not isinstance(payload.get("fallback_reason"), str)
        )
        or payload.get("worker_bound")
        not in {"worker_limit", "work", "memory", "pixels"}
        or not isinstance(payload.get("available_memory_bytes"), int)
        or not isinstance(payload.get("estimated_worker_bytes"), int)
        or not isinstance(payload.get("seek_seconds"), (int, float))
        or not isinstance(payload.get("capture_seconds"), (int, float))
        or not isinstance(payload.get("queue_wait_seconds"), (int, float))
        or not isinstance(payload.get("frame_time_p50_ms"), (int, float))
        or not isinstance(payload.get("frame_time_p95_ms"), (int, float))
        or not isinstance(payload.get("browser_launches"), int)
        or not isinstance(payload.get("failed_render_count"), int)
    ):
        raise RuntimeError(f"Web render worker {workers} returned incomplete metrics")
    return cast(RenderResult, payload)


def _new_run_dir(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    path = root / f"r-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    return path


def verify(frame_count: int, run_root: Path) -> int:
    if frame_count < 151:
        raise ValueError("Web render verification needs at least 151 frames")
    from mediaflow.infrastructure.runtime_paths import RuntimePaths

    run_dir = _new_run_dir(run_root)
    script = Path(__file__).resolve()
    serial = _child_result(script, run_dir, 1, frame_count)
    parallel = _child_result(script, run_dir, 4, frame_count)
    ffmpeg = RuntimePaths.discover().ffmpeg
    serial_cache = Path(serial["cache"])
    parallel_cache = Path(parallel["cache"])
    serial_hashes = _frame_hashes(ffmpeg, serial_cache)
    parallel_hashes = _frame_hashes(ffmpeg, parallel_cache)
    identical_frames = sum(
        left == right
        for left, right in zip(serial_hashes, parallel_hashes, strict=False)
    )
    minimum_frame_psnr_db = _minimum_frame_psnr(
        ffmpeg,
        serial_cache,
        parallel_cache,
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
    )
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
        "required_minimum_frame_psnr_db": MIN_FRAME_PSNR_DB,
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
    return verify(arguments.frames, arguments.run_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
