from __future__ import annotations

from scripts.verify_preview_performance import preview_requirements_met


def _requirements(**overrides: object) -> bool:
    values: dict[str, object] = {
        "open_seconds": 0.4,
        "startup_seconds": 0.6,
        "first_window_advanced_frames": 300,
        "first_window_expected_frames": 300,
        "first_window_presented_frames": 300,
        "first_window_visible_frames": 300,
        "first_window_dropped_frames": 0,
        "final_advanced_frames": 600,
        "final_expected_frames": 600,
        "final_presented_frames": 600,
        "final_visible_frames": 600,
        "final_dropped_frames": 0,
        "presentation_p95_seconds": 0.04,
        "presentation_max_seconds": 0.06,
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


def test_preview_contract_rejects_a_clock_that_advances_without_visible_frames() -> None:
    assert not _requirements(
        first_window_visible_frames=0,
        final_visible_frames=0,
    )


def test_preview_contract_rejects_irregular_presentation_cadence() -> None:
    assert not _requirements(presentation_max_seconds=0.2)
