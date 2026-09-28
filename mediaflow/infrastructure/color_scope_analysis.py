from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage

from mediaflow.domain.color_scopes import ColorScopeAnalysis, ColorScopeStatistics
from mediaflow.file_digest import sha256_file


class ColorScopeAnalyzer:
    """Calculate deterministic histograms, waveforms, RGB parade, and vectorscope density."""

    def analyze(self, path: str | Path, *, frame: int, bins: int = 64) -> ColorScopeAnalysis:
        source = Path(path).resolve(strict=True)
        image = QImage(str(source))
        if image.isNull():
            raise ValueError(f"无法读取示波器画面：{source}")
        image = image.convertToFormat(QImage.Format.Format_RGB888)
        width = image.width()
        height = image.height()
        bytes_per_line = image.bytesPerLine()
        payload = np.frombuffer(image.bits(), dtype=np.uint8, count=height * bytes_per_line)
        rgb = payload.reshape(height, bytes_per_line)[:, : width * 3].reshape(height, width, 3)
        sampled = rgb[:: max(1, height // 540), :: max(1, width // 960)].astype(np.float32)
        red = sampled[:, :, 0]
        green = sampled[:, :, 1]
        blue = sampled[:, :, 2]
        luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue

        histogram_red = np.bincount(red.astype(np.uint8).ravel(), minlength=256)
        histogram_green = np.bincount(green.astype(np.uint8).ravel(), minlength=256)
        histogram_blue = np.bincount(blue.astype(np.uint8).ravel(), minlength=256)
        histogram_luma = np.bincount(np.clip(luma, 0, 255).astype(np.uint8).ravel(), minlength=256)

        sampled_height, sampled_width = luma.shape
        x_bins = np.minimum(
            bins - 1,
            (np.arange(sampled_width, dtype=np.int32) * bins // sampled_width),
        )
        waveform = self._waveform_density(luma, x_bins=x_bins, bins=bins)
        waveform_red = self._waveform_density(red, x_bins=x_bins, bins=bins)
        waveform_green = self._waveform_density(green, x_bins=x_bins, bins=bins)
        waveform_blue = self._waveform_density(blue, x_bins=x_bins, bins=bins)

        cb = np.clip(128.0 + (blue - luma) * 0.565, 0, 255)
        cr = np.clip(128.0 + (red - luma) * 0.713, 0, 255)
        vector_x = np.minimum(bins - 1, cb.astype(np.int32) * bins // 256)
        vector_y = bins - 1 - np.minimum(bins - 1, cr.astype(np.int32) * bins // 256)
        vector_index = vector_y * bins + vector_x
        vectorscope = np.bincount(vector_index.ravel(), minlength=bins * bins).reshape(bins, bins)

        pixel_count = float(sampled_height * sampled_width)
        return ColorScopeAnalysis(
            frame=frame,
            source_path=str(source),
            source_sha256=sha256_file(source),
            width=width,
            height=height,
            bins=bins,
            histogram_red=histogram_red.tolist(),
            histogram_green=histogram_green.tolist(),
            histogram_blue=histogram_blue.tolist(),
            histogram_luma=histogram_luma.tolist(),
            waveform_luma=self._normalized_density(waveform),
            waveform_red=self._normalized_density(waveform_red),
            waveform_green=self._normalized_density(waveform_green),
            waveform_blue=self._normalized_density(waveform_blue),
            vectorscope=self._normalized_density(vectorscope),
            statistics=ColorScopeStatistics(
                average_luma=float(luma.mean() / 255.0),
                black_clip_percent=float(np.count_nonzero(luma <= 1.0) * 100.0 / pixel_count),
                white_clip_percent=float(np.count_nonzero(luma >= 254.0) * 100.0 / pixel_count),
            ),
        )

    @staticmethod
    def _waveform_density(
        values: np.ndarray,
        *,
        x_bins: np.ndarray,
        bins: int,
    ) -> np.ndarray:
        y_bins = bins - 1 - np.minimum(
            bins - 1,
            np.clip(values, 0, 255).astype(np.int32) * bins // 256,
        )
        indices = y_bins * bins + x_bins[np.newaxis, :]
        return np.bincount(indices.ravel(), minlength=bins * bins).reshape(bins, bins)

    @staticmethod
    def _normalized_density(values: np.ndarray) -> list[list[int]]:
        peak = int(values.max())
        if peak <= 0:
            return values.astype(np.int32).tolist()
        return np.rint(values.astype(np.float64) * (1000.0 / peak)).astype(np.int32).tolist()
