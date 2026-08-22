# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_artifacts import verification_run, verification_workspace_root

FIXTURE = ROOT / "tests" / "fixtures" / "editable-media-v6"
MINIMUM_PSNR_DB = 38.0
MINIMUM_PRODUCTION_SPEEDUP = 1.10


def _prepare_fixture(run_dir: Path, canvas_scale: int) -> tuple[Path, str]:
    if canvas_scale == 1:
        return FIXTURE, "landscape"
    if canvas_scale != 2:
        raise ValueError("The production verifier supports 1x or 2x canvas scale")
    fixture = run_dir / "editable-media-v6-4k"
    shutil.copytree(FIXTURE, fixture)
    manifest_path = fixture / "editable-media.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    landscape = next(
        item for item in manifest["variants"] if item["id"] == "landscape"
    )
    scaled = json.loads(json.dumps(landscape))
    scaled["id"] = "benchmark-4k"
    scaled["name"] = "Benchmark 4K"
    for field in ("width", "height"):
        scaled["canvas"][field] = int(scaled["canvas"][field]) * canvas_scale
    for layer in scaled["layers"].values():
        for field in ("x", "y", "width", "height"):
            layer[field] = int(layer[field]) * canvas_scale
    manifest["variants"].append(scaled)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return fixture, "benchmark-4k"


def _probe_video(ffprobe: Path, video: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            (
                "stream=codec_name,pix_fmt,width,height,r_frame_rate,avg_frame_rate,"
                "nb_read_frames,color_range,color_space,color_transfer,color_primaries"
            ),
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    payload = json.loads(completed.stdout)
    stream = payload["streams"][0]
    return {
        "codec_name": stream.get("codec_name"),
        "pixel_format": stream.get("pix_fmt"),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "r_frame_rate": stream.get("r_frame_rate"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
        "frame_count": int(stream.get("nb_read_frames") or 0),
        "duration_seconds": float(payload["format"]["duration"]),
        "color_range": stream.get("color_range"),
        "color_space": stream.get("color_space"),
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
    }


def _minimum_frame_psnr(
    ffmpeg: Path,
    reference: Path,
    candidate: Path,
    *,
    width: int,
    height: int,
    frame_count: int,
) -> float:
    """Compare decode-order frames without trusting container timestamp rounding."""

    command = [
        str(ffmpeg),
        "-v",
        "error",
        "-i",
        "",
        "-vsync",
        "0",
        "-frames:v",
        str(frame_count),
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "-",
    ]
    reference_command = [*command]
    reference_command[4] = str(reference)
    candidate_command = [*command]
    candidate_command[4] = str(candidate)
    reference_process = subprocess.Popen(
        reference_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    candidate_process = subprocess.Popen(
        candidate_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if reference_process.stdout is None or candidate_process.stdout is None:
        raise RuntimeError("FFmpeg raw frame comparison pipes are unavailable")
    frame_bytes = width * height * 3
    minimum = float("inf")
    try:
        for frame_index in range(frame_count):
            left = reference_process.stdout.read(frame_bytes)
            right = candidate_process.stdout.read(frame_bytes)
            if len(left) != frame_bytes or len(right) != frame_bytes:
                raise RuntimeError(
                    "Direct encode comparison returned an incomplete frame: "
                    f"frame={frame_index}, reference={len(left)}, candidate={len(right)}"
                )
            difference = np.frombuffer(left, dtype=np.uint8).astype(np.int16)
            difference -= np.frombuffer(right, dtype=np.uint8).astype(np.int16)
            squared = np.multiply(difference, difference, dtype=np.int32)
            mean_squared_error = float(np.mean(squared))
            value = (
                float("inf")
                if mean_squared_error == 0
                else 20 * math.log10(255) - 10 * math.log10(mean_squared_error)
            )
            minimum = min(minimum, value)
        reference_process.stdout.read()
        candidate_process.stdout.read()
    finally:
        reference_process.stdout.close()
        candidate_process.stdout.close()
        reference_returncode = reference_process.wait()
        candidate_returncode = candidate_process.wait()
    if reference_returncode != 0 or candidate_returncode != 0:
        raise RuntimeError(
            "FFmpeg raw frame comparison failed: "
            f"reference={reference_returncode}, candidate={candidate_returncode}"
        )
    return minimum


def _create_project(
    run_dir: Path,
    frame_count: int,
    *,
    source: Path,
    variant_id: str,
    fps_numerator: int,
    fps_denominator: int,
):
    from mediaflow.application.timeline_editor import TimelineEditor
    from mediaflow.application.web_media_service import WebMediaServices
    from mediaflow.domain.enums import TrackKind
    from mediaflow.domain.project import ProjectProfile
    from mediaflow.infrastructure.editable_media_contract import editable_media_contract
    from mediaflow.infrastructure.project_repository import ProjectRepository
    from mediaflow.infrastructure.runtime_context import RuntimeContext
    from mediaflow.infrastructure.structured_file_reader import LocalStructuredFileReader
    from mediaflow.infrastructure.web_browser import BrowserWebPackageValidator
    from mediaflow.infrastructure.web_package_storage import LocalWebPackageStorage

    paths = RuntimeContext.discover().paths
    repository = ProjectRepository.create(
        verification_workspace_root(run_dir) / "project",
        "WebCodecs direct encode benchmark",
        ProjectProfile(
            width=3840 if variant_id == "benchmark-4k" else 1920,
            height=2160 if variant_id == "benchmark-4k" else 1080,
            fps_numerator=fps_numerator,
            fps_denominator=fps_denominator,
        ),
    )
    project = repository.projects.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    services = WebMediaServices(
        repository,
        lambda _sequence_id: editor,
        BrowserWebPackageValidator(paths.chromium, editable_media_contract()),
        LocalStructuredFileReader(),
        LocalWebPackageStorage(),
        editable_media_contract(),
    )
    asset = services.packages.import_package(source)
    track = editor.add_track(TrackKind.VIDEO)
    clip = editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=frame_count,
    )
    services.clips.select_variant(
        project.main_sequence_id,
        clip.id,
        variant_id,
        expected_revision=0,
    )
    timeline = repository.timeline.load_timeline(project.main_sequence_id)
    return repository, asset, clip, timeline, paths


def verify(
    frame_count: int,
    run_dir: Path,
    *,
    fps_numerator: int,
    fps_denominator: int,
    canvas_scale: int,
) -> int:
    if frame_count < 30:
        raise ValueError("Direct encode verification needs at least 30 frames")
    from mediaflow.infrastructure.web_capture_engine import web_capture_diagnostics
    from mediaflow.infrastructure.web_render_service import WebRenderService

    fixture, variant_id = _prepare_fixture(run_dir, canvas_scale)
    repository, asset, clip, timeline, paths = _create_project(
        run_dir,
        frame_count,
        source=fixture,
        variant_id=variant_id,
        fps_numerator=fps_numerator,
        fps_denominator=fps_denominator,
    )
    try:
        if paths.chromium is None:
            raise FileNotFoundError("Pinned Chromium is unavailable")
        service = WebRenderService(repository, paths)
        target = service.cache.target(timeline, clip, asset)
        previous_policy = os.environ.get("MEDIAFLOW_WEB_DIRECT_H264")
        try:
            os.environ["MEDIAFLOW_WEB_DIRECT_H264"] = "0"
            baseline_started = time.perf_counter()
            baseline_cache = service.render_clip(timeline, clip.id)
            baseline_seconds = time.perf_counter() - baseline_started
            baseline_metrics = web_capture_diagnostics(paths.chromium).last_metrics
            if baseline_metrics is None:
                raise RuntimeError("The frame-pipe reference returned no capture metrics")
            reference = run_dir / "reference-frame-pipe.mkv"
            shutil.copy2(baseline_cache, reference)

            os.environ["MEDIAFLOW_WEB_DIRECT_H264"] = "1"
            candidate_started = time.perf_counter()
            candidate_cache = service.render_clip(timeline, clip.id)
            candidate_seconds = time.perf_counter() - candidate_started
            candidate_path = run_dir / "candidate-production.mkv"
            shutil.copy2(candidate_cache, candidate_path)
            manifest = json.loads(target.manifest_path.read_text(encoding="utf-8"))
            reuse_started = time.perf_counter()
            reused = service.render_clip(timeline, clip.id)
            reuse_seconds = time.perf_counter() - reuse_started
            if reused != candidate_cache:
                raise RuntimeError("The production direct cache was not reused deterministically")
        finally:
            if previous_policy is None:
                os.environ.pop("MEDIAFLOW_WEB_DIRECT_H264", None)
            else:
                os.environ["MEDIAFLOW_WEB_DIRECT_H264"] = previous_policy

        candidate_probe = _probe_video(paths.ffprobe, candidate_path)
        reference_probe = _probe_video(paths.ffprobe, reference)
        minimum_psnr_db = _minimum_frame_psnr(
            paths.ffmpeg,
            reference,
            candidate_path,
            width=target.width,
            height=target.height,
            frame_count=frame_count,
        )
        capture = manifest.get("capture") or {}
        encoder = capture.get("encoder") or {}
        production_probe = manifest.get("probe") or {}
        bounded = (
            int(encoder.get("maximum_encode_queue_size") or 999) <= 4
            and int(encoder.get("maximum_pending_writes") or 999) <= 4
        )
        encoder_identity_passed = (
            encoder.get("requested_hardware_acceleration") == "prefer-hardware"
            and encoder.get("hardware_acceleration_verified") is True
            and encoder.get("actual_encoder_name")
            == "MediaFoundationVideoEncodeAccelerator"
            and encoder.get("actual_encoder_type") == "hardware"
        )
        structural_passed = (
            capture.get("planned_backend") == "webcodecs-h264"
            and capture.get("actual_backend") == "webcodecs-h264"
            and capture.get("fallback_reason") is None
            and candidate_probe["frame_count"] == frame_count
            and candidate_probe["width"] == target.width
            and candidate_probe["height"] == target.height
            and candidate_probe["codec_name"] == "h264"
            and candidate_probe["pixel_format"] == "yuv420p"
            and abs(
                Fraction(str(candidate_probe["r_frame_rate"]))
                - Fraction(
                    timeline.sequence.profile.fps_numerator,
                    timeline.sequence.profile.fps_denominator,
                )
            )
            <= Fraction(
                timeline.sequence.profile.fps_numerator,
                timeline.sequence.profile.fps_denominator,
            )
            / 1_000_000
            and (
                candidate_probe["color_space"],
                candidate_probe["color_transfer"],
                candidate_probe["color_primaries"],
            )
            == ("bt709", "bt709", "bt709")
            and encoder.get("encoded_chunks") == frame_count
            and encoder.get("timestamps_monotonic") is True
            and encoder.get("exact_frame_time_boundaries") is True
            and encoder.get("attestation_method") == "chromium-trace"
            and encoder_identity_passed
            and isinstance(encoder.get("attested_frames"), int)
            and 1 <= encoder["attested_frames"] <= 8
            and encoder.get("platform_encode_events", 0)
            >= encoder["attested_frames"]
            and encoder.get("platform_output_events", 0)
            >= encoder["attested_frames"]
            and encoder.get("zero_copy_verified") is False
            and encoder.get("input_copy_path")
            in {"gpu-readback-to-memory", "unknown"}
            and encoder.get("encoder_storage_type") == "unknown"
            and (
                encoder.get("input_copy_path") == "unknown"
                or encoder.get("gpu_readback_events", 0)
                >= encoder["attested_frames"]
            )
            and production_probe.get("packet_pts_monotonic") is True
            and production_probe.get("packet_dts_monotonic") is True
            and isinstance(
                production_probe.get("maximum_video_clock_error_microseconds"),
                int,
            )
            and 0
            <= production_probe["maximum_video_clock_error_microseconds"]
            <= 1_000
            and production_probe.get("audio_video_end_drift_microseconds") is None
            and bounded
        )
        visual_passed = minimum_psnr_db >= MINIMUM_PSNR_DB
        speedup = baseline_seconds / candidate_seconds
        performance_passed = speedup >= MINIMUM_PRODUCTION_SPEEDUP
        passed = structural_passed and visual_passed and performance_passed
        report = {
            "schema": "mediaflow-webcodecs-direct-encode-production/v3",
            "status": "passed" if passed else "failed",
            "frame_count": frame_count,
            "fps": f"{fps_numerator}/{fps_denominator}",
            "canvas_scale": canvas_scale,
            "fixture": str(fixture),
            "reference": {
                "path": str(reference),
                "seconds": baseline_seconds,
                "throughput_fps": frame_count / baseline_seconds,
                "capture_backend": baseline_metrics.capture_backend,
                "capture_backend_reason": baseline_metrics.capture_backend_reason,
                "probe": reference_probe,
            },
            "candidate": {
                "path": str(candidate_path),
                "seconds": candidate_seconds,
                "throughput_fps": frame_count / candidate_seconds,
                "cache_reuse_seconds": reuse_seconds,
                "capture": capture,
                "probe": candidate_probe,
            },
            "speedup": speedup,
            "performance": {
                "required_minimum_speedup": MINIMUM_PRODUCTION_SPEEDUP,
                "passed": performance_passed,
            },
            "visual": {
                "minimum_frame_psnr_db": minimum_psnr_db,
                "required_minimum_frame_psnr_db": MINIMUM_PSNR_DB,
                "passed": visual_passed,
            },
            "structural_passed": structural_passed,
            "bounded_pipeline": bounded,
            "production_adoption": {
                "decision": "ready-for-eligible-clips" if passed else "blocked",
                "eligible_scope": [
                    "animated opaque SDR BT.709 UHD 3840x2160 or 2160x3840 editable-media web clips",
                    "30 or 29.97 fps only",
                    "even canvas dimensions with enough pixel work to recover verification startup",
                    "no native-underlay video",
                ],
                "documented_constraints": [
                    "H.264 discards alpha; transparent and native-underlay clips keep FFV1 BGRA",
                    "Chromium trace must prove the Windows Media Foundation hardware encoder",
                    "NVIDIA utilization or memory pressure at 90 percent blocks the direct attempt",
                    "the current Canvas VideoFrame path is hardware encoded but still performs "
                    "a GPU readback into memory, so it is not reported as zero-copy",
                    "runtime verification failure atomically reruns the complete frame-pipe backend",
                ],
            },
        }
        report_path = run_dir / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"REPORT={report_path}")
        if not passed:
            raise RuntimeError(f"Direct H.264 production verification failed: {report_path}")
        return 0
    finally:
        repository.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--fps-numerator", type=int, default=30)
    parser.add_argument("--fps-denominator", type=int, default=1)
    parser.add_argument("--canvas-scale", type=int, choices=(1, 2), default=2)
    parser.add_argument(
        "--run-root",
        type=Path,
        help="Optional managed evidence root; the default remains under MEDIAFLOW_TEST_ROOT.",
    )
    arguments = parser.parse_args(argv)
    managed_root = arguments.run_root.resolve() if arguments.run_root is not None else None
    with verification_run(
        "webcodecs-direct-encode",
        managed_root=managed_root,
    ) as run_dir:
        return verify(
            arguments.frames,
            run_dir,
            fps_numerator=arguments.fps_numerator,
            fps_denominator=arguments.fps_denominator,
            canvas_scale=arguments.canvas_scale,
        )


if __name__ == "__main__":
    raise SystemExit(main())
