from __future__ import annotations

from scripts.verify_preview_performance import preview_requirements_met


def _requirements(**overrides: object) -> bool:
    values: dict[str, object] = {
        "open_seconds": 0.4,
        "startup_seconds": 0.6,
        "first_window_advanced_frames": 300,
        "first_window_expected_frames": 300,
        "first_window_dropped_frames": 0,
        "final_advanced_frames": 600,
        "final_expected_frames": 600,
        "final_dropped_frames": 0,
    }
    values.update(overrides)
    return preview_requirements_met(**values)  # type: ignore[arg-type]


def test_preview_contract_accepts_complete_drop_free_playback() -> None:
    assert _requirements()


def test_preview_contract_rejects_playback_that_freezes_after_ten_seconds() -> None:
    assert not _requirements(
        first_window_advanced_frames=300,
        final_advanced_frames=300,
    )


def test_preview_contract_rejects_drops_after_the_first_window() -> None:
    assert not _requirements(
        first_window_dropped_frames=0,
        final_dropped_frames=1,
    )
