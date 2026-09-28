from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from mediaflow.infrastructure.color_scope_analysis import ColorScopeAnalyzer


def _write_scope_fixture(path: Path) -> None:
    image = QImage(64, 32, QImage.Format.Format_RGB888)
    for y in range(image.height()):
        for x in range(image.width()):
            if x < image.width() // 2:
                color = QColor(0, 0, 0)
            elif y < image.height() // 2:
                color = QColor(255, 255, 255)
            else:
                color = QColor(255, 0, 0)
            image.setPixelColor(x, y, color)
    assert image.save(str(path), "PNG")


def test_color_scope_analyzer_reports_deterministic_pixel_evidence(tmp_path: Path) -> None:
    frame_path = tmp_path / "scope-frame.png"
    _write_scope_fixture(frame_path)

    analysis = ColorScopeAnalyzer().analyze(frame_path, frame=27, bins=32)

    assert analysis.frame == 27
    assert analysis.width == 64
    assert analysis.height == 32
    assert len(analysis.source_sha256) == 64
    assert sum(analysis.histogram_red) == 64 * 32
    assert sum(analysis.histogram_green) == 64 * 32
    assert sum(analysis.histogram_blue) == 64 * 32
    assert sum(analysis.histogram_luma) == 64 * 32
    assert analysis.histogram_red[0] == 64 * 32 // 2
    assert analysis.histogram_red[255] == 64 * 32 // 2
    assert analysis.statistics.black_clip_percent == pytest.approx(50.0)
    assert analysis.statistics.white_clip_percent == pytest.approx(25.0)
    assert len(analysis.waveform_luma) == 32
    assert all(len(row) == 32 for row in analysis.waveform_luma)
    assert max(max(row) for row in analysis.waveform_luma) == 1000
    for parade in (
        analysis.waveform_red,
        analysis.waveform_green,
        analysis.waveform_blue,
    ):
        assert len(parade) == 32
        assert all(len(row) == 32 for row in parade)
        assert max(max(row) for row in parade) == 1000
    assert len(analysis.vectorscope) == 32
    assert all(len(row) == 32 for row in analysis.vectorscope)
    assert max(max(row) for row in analysis.vectorscope) == 1000
