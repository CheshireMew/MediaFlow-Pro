from __future__ import annotations

import hashlib
import json
from array import array
from collections.abc import Callable
from pathlib import Path

from mediaflow.application.ports import AssetProcessingDocuments
from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset
from mediaflow.domain.storage_names import require_windows_interop_path

from .ffmpeg_runner import FfmpegRunner
from .runtime_paths import RuntimePaths


class WaveformService:
    CACHE_VERSION = 2
    SAMPLE_RATE = 8_000
    BLOCK_SIZES = (128, 512, 2048, 8192)

    def __init__(
        self,
        repository: AssetProcessingDocuments,
        paths: RuntimePaths | None = None,
    ):
        self.repository = repository
        self.paths = paths or RuntimePaths.discover()
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)

    def generate(
        self,
        asset: Asset,
        *,
        duration_seconds: float,
        progress: Callable[[OperationProgress], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Asset:
        source = self.repository.catalog.resolve_asset_path(asset)
        if not source.is_file():
            raise FileNotFoundError(source)
        source = require_windows_interop_path(source)
        output_dir = self.repository.project_dir / "cache" / "waveforms"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{asset.id}-{self._cache_key(asset, source)}.json"
        decoded = unique_temporary_sibling(
            output_dir / f"{asset.id}.pcm",
            label="decoding",
        )
        on_position: Callable[[float], None] | None = None
        if duration_seconds <= 0:
            if progress:
                progress(OperationProgress.indeterminate("waveform_decoding"))
        elif progress is not None:

            def report_position(position: float) -> None:
                progress(
                    OperationProgress.determinate(
                        "waveform_decoding",
                        completed=position,
                        total=duration_seconds,
                        unit="media_seconds",
                    )
                )

            on_position = report_position
        try:
            result = self.ffmpeg.run_progress(
                [
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    str(self.SAMPLE_RATE),
                    "-f",
                    "s16le",
                    str(decoded),
                ],
                total_seconds=duration_seconds,
                on_position=on_position,
                timeout=300,
                check_cancelled=check_cancelled,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Waveform decode failed: {result.stderr}")
            samples = array("h")
            with decoded.open("rb") as source_stream:
                samples.fromfile(source_stream, decoded.stat().st_size // samples.itemsize)
        finally:
            decoded.unlink(missing_ok=True)

        total_samples = max(1, len(samples) * len(self.BLOCK_SIZES))
        processed_samples = 0
        levels: dict[str, list[list[float]]] = {}
        for block_size in self.BLOCK_SIZES:
            peaks, consumed = self._peaks(
                samples,
                block_size,
                processed=processed_samples,
                total=total_samples,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            levels[str(block_size)] = peaks
            processed_samples += consumed
        if progress:
            progress(OperationProgress.indeterminate("waveform_saving"))
        atomic_write_text(
            output,
            json.dumps(
                {"sample_rate": self.SAMPLE_RATE, "sample_count": len(samples), "levels": levels},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        return self.repository.catalog.set_asset_waveform_path(
            asset.id,
            expected_fingerprint=asset.fingerprint,
            waveform_path=output,
        )

    @classmethod
    def _cache_key(cls, asset: Asset, source: Path) -> str:
        source_stat = source.stat()
        payload = {
            "version": cls.CACHE_VERSION,
            "fingerprint": (
                asset.fingerprint.model_dump(mode="json")
                if asset.fingerprint is not None
                else None
            ),
            "source_size": source_stat.st_size,
            "source_modified_ns": source_stat.st_mtime_ns,
            "sample_rate": cls.SAMPLE_RATE,
            "block_sizes": cls.BLOCK_SIZES,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _peaks(
        samples: array,
        block_size: int,
        *,
        processed: int,
        total: int,
        progress: Callable[[OperationProgress], None] | None,
        check_cancelled: Callable[[], None] | None,
    ) -> tuple[list[list[float]], int]:
        peaks: list[list[float]] = []
        scale = 1.0 / 32768.0
        consumed = 0
        for offset in range(0, len(samples), block_size):
            if check_cancelled and offset % (block_size * 256) == 0:
                check_cancelled()
            block = samples[offset : offset + block_size]
            if not block:
                continue
            peaks.append([min(block) * scale, max(block) * scale])
            consumed += len(block)
            if progress and len(peaks) % 256 == 0:
                progress(
                    OperationProgress.determinate(
                        "waveform_calculating",
                        completed=min(total, processed + consumed),
                        total=total,
                        unit="samples",
                    )
                )
        if progress:
            progress(
                OperationProgress.determinate(
                    "waveform_calculating",
                    completed=min(total, processed + consumed),
                    total=total,
                    unit="samples",
                )
            )
        return peaks, consumed
