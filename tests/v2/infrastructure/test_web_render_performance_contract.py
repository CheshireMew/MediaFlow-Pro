from __future__ import annotations

from scripts.verify_web_render_performance import web_render_requirements_met


def _requirements(**overrides: object) -> bool:
    values: dict[str, object] = {
        "frame_count": 180,
        "serial_seconds": 30.0,
        "parallel_seconds": 20.0,
        "serial_frame_count": 180,
        "parallel_frame_count": 180,
        "minimum_frame_psnr_db": 80.0,
        "parallel_workers": 2,
    }
    values.update(overrides)
    return web_render_requirements_met(**values)  # type: ignore[arg-type]


def test_web_render_contract_accepts_faster_pixel_identical_output() -> None:
    assert _requirements()


def test_web_render_contract_rejects_missing_frames() -> None:
    assert not _requirements(parallel_frame_count=179)


def test_web_render_contract_rejects_visible_pixel_drift() -> None:
    assert not _requirements(minimum_frame_psnr_db=45.0)


def test_web_render_contract_rejects_serial_execution() -> None:
    assert not _requirements(parallel_workers=1)


def test_web_render_contract_rejects_insufficient_speedup() -> None:
    assert not _requirements(parallel_seconds=25.0)
