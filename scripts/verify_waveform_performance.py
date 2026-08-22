# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import AssetKind
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.waveform_service import WaveformService
from mediaflow.waveform_cache import (
    inspect_waveform_cache,
    waveform_cache_size,
)

DEFAULT_DURATION_SECONDS = 4 * 60 * 60
MEMORY_DELTA_LIMIT_BYTES = 128 * 1024 * 1024
MINIMUM_MEDIA_SECONDS_PER_WALL_SECOND = 1_000


def _process_tree_rss(process: psutil.Process) -> int:
    processes = [process]
    try:
        processes.extend(process.children(recursive=True))
    except psutil.Error:
        pass
    total = 0
    for item in processes:
        try:
            total += item.memory_info().rss
        except psutil.Error:
            continue
    return total


def _create_fixture(root: Path, duration_seconds: int) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=False)
    source = root / "four-hour-audio.flac"
    ffmpeg = RuntimeContext.discover().paths.ffmpeg
    generated = subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=8000:duration={duration_seconds}",
            "-c:a",
            "flac",
            "-compression_level",
            "0",
            str(source),
        ],
        capture_output=True,
        timeout=600,
        check=False,
    )
    if generated.returncode != 0:
        raise RuntimeError(generated.stderr.decode(errors="replace"))
    project_dir = root / "Waveform Project"
    with ProjectRepository.create(project_dir, "Waveform Project") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.AUDIO)
    return project_dir, asset.id


def _child(project_dir: Path, asset_id: str, duration_seconds: int) -> None:
    with ProjectRepository.open(project_dir, writable=True) as repository:
        asset = repository.assets.get_asset(asset_id)
        print("READY", flush=True)
        if sys.stdin.readline().strip() != "GO":
            raise RuntimeError("Waveform verifier parent did not start the measurement")
        started = time.perf_counter()
        output = WaveformService(repository, RuntimeContext.discover().paths).prepare(
            asset,
            duration_seconds=duration_seconds,
        )
        elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "seconds": elapsed,
                "output": str(output),
                "output_bytes": output.stat().st_size,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def verify(root: Path, duration_seconds: int) -> dict[str, object]:
    project_dir, asset_id = _create_fixture(root, duration_seconds)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.verify_waveform_performance",
            "--child",
            "--project",
            str(project_dir),
            "--asset-id",
            asset_id,
            "--duration-seconds",
            str(duration_seconds),
        ],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None
    ready = process.stdout.readline().strip()
    if ready != "READY":
        _stdout, stderr = process.communicate(timeout=30)
        raise RuntimeError(f"Waveform verifier child failed before measurement: {ready}\n{stderr}")
    observed = psutil.Process(process.pid)
    baseline_rss = _process_tree_rss(observed)
    peak_rss = baseline_rss
    process.stdin.write("GO\n")
    process.stdin.flush()
    process.stdin.close()
    process.stdin = None
    while process.poll() is None:
        peak_rss = max(peak_rss, _process_tree_rss(observed))
        time.sleep(0.05)
    stdout, stderr = process.communicate(timeout=30)
    if process.returncode != 0:
        raise RuntimeError(stderr or stdout)
    child = json.loads(stdout.strip().splitlines()[-1])
    output = Path(str(child["output"]))
    header = inspect_waveform_cache(output)
    sample_count = header.sample_count
    level_counts = {str(level.block_size): level.count for level in header.levels}
    expected_counts = {
        str(block_size): math.ceil(sample_count / block_size)
        for block_size in WaveformService.BLOCK_SIZES
    }
    memory_delta = max(0, peak_rss - baseline_rss)
    maximum_seconds = max(5.0, duration_seconds / MINIMUM_MEDIA_SECONDS_PER_WALL_SECOND)
    expected_output_bytes = waveform_cache_size(level_counts)
    report: dict[str, object] = {
        "status": "passed",
        "duration_seconds": duration_seconds,
        "seconds": child["seconds"],
        "maximum_seconds": maximum_seconds,
        "baseline_rss_bytes": baseline_rss,
        "peak_process_tree_rss_bytes": peak_rss,
        "memory_delta_bytes": memory_delta,
        "memory_delta_limit_bytes": MEMORY_DELTA_LIMIT_BYTES,
        "sample_rate": header.sample_rate,
        "sample_count": sample_count,
        "level_counts": level_counts,
        "expected_level_counts": expected_counts,
        "output": child["output"],
        "output_bytes": child["output_bytes"],
        "expected_output_bytes": expected_output_bytes,
    }
    if memory_delta >= MEMORY_DELTA_LIMIT_BYTES:
        report["status"] = "failed"
    if level_counts != expected_counts:
        report["status"] = "failed"
    if float(child["seconds"]) > maximum_seconds:
        report["status"] = "failed"
    if int(child["output_bytes"]) != expected_output_bytes:
        report["status"] = "failed"
    report_path = root / "waveform-performance-report.json"
    report["report"] = str(report_path)
    atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--asset-id", default="")
    args = parser.parse_args()
    if args.child:
        if args.project is None or not args.asset_id:
            raise ValueError("Child mode requires --project and --asset-id")
        _child(args.project, args.asset_id, args.duration_seconds)
        return
    if args.root is None:
        raise ValueError("--root is required")
    print(json.dumps(verify(args.root.resolve(), args.duration_seconds), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
