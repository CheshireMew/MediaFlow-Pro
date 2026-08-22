from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_web_render_performance import (
    _balanced_baseline_comparison,
    web_render_requirements_met,
)


def _requirements(**overrides: object) -> bool:
    values: dict[str, object] = {
        "frame_count": 180,
        "serial_seconds": 30.0,
        "parallel_seconds": 20.0,
        "serial_frame_count": 180,
        "parallel_frame_count": 180,
        "identical_frames": 180,
        "minimum_frame_psnr_db": 80.0,
        "parallel_workers": 2,
        "parallel_fast_capture_workers": 2,
        "parallel_capture_backend": "drawelement",
        "slow_modulo_seconds": 10.0,
        "slow_dynamic_seconds": 7.0,
    }
    values.update(overrides)
    return web_render_requirements_met(**values)  # type: ignore[arg-type]


def test_web_render_contract_accepts_faster_pixel_identical_output() -> None:
    assert _requirements()


def test_web_render_contract_rejects_missing_frames() -> None:
    assert not _requirements(parallel_frame_count=179)


def test_web_render_contract_rejects_visible_pixel_drift() -> None:
    assert not _requirements(minimum_frame_psnr_db=45.0)


def test_web_render_contract_rejects_any_lossless_frame_difference() -> None:
    assert not _requirements(identical_frames=179)


def test_web_render_contract_rejects_serial_execution() -> None:
    assert not _requirements(parallel_workers=1)


def test_web_render_contract_rejects_insufficient_speedup() -> None:
    assert not _requirements(parallel_seconds=25.0)


def test_web_render_contract_rejects_screenshot_backend_regression() -> None:
    assert not _requirements(
        parallel_fast_capture_workers=0,
        parallel_capture_backend="screenshot",
    )


def test_web_render_contract_rejects_partial_fast_worker_consensus() -> None:
    assert not _requirements(parallel_fast_capture_workers=1)


def test_web_render_contract_rejects_insufficient_slow_frame_improvement() -> None:
    assert not _requirements(slow_dynamic_seconds=8.5)


def test_balanced_baseline_rejects_total_or_p95_regressions_over_ten_percent(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "frame_count": 180,
                "parallel": {
                    "seconds": 10.0,
                    "frame_time_p95_ms": 80.0,
                    "worker_count": 2,
                    "capture_backend": "drawelement",
                },
            }
        ),
        encoding="utf-8",
    )
    current = {
        "seconds": 10.5,
        "frame_time_p95_ms": 86.0,
        "worker_count": 3,
        "capture_backend": "drawelement",
    }
    accepted = _balanced_baseline_comparison(
        baseline_path,
        frame_count=180,
        current=current,  # type: ignore[arg-type]
    )
    rejected = _balanced_baseline_comparison(
        baseline_path,
        frame_count=180,
        current={**current, "frame_time_p95_ms": 89.0},  # type: ignore[arg-type]
    )

    assert accepted["passed"] is True
    assert accepted["baseline_worker_count"] == 2
    assert accepted["current_worker_count"] == 3
    assert rejected["passed"] is False
