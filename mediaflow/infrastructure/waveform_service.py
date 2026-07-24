from __future__ import annotations

import json
from array import array
from collections.abc import Callable

from mediaflow.application.ports import AssetProcessingDocuments
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset

from .process_observers import FfmpegProgressObserver, ffmpeg_progress_command
from .runtime_paths import RuntimePaths
from .subprocess_runner import run_cancellable_streaming


class WaveformService:
    SAMPLE_RATE = 8_000
    BLOCK_SIZES = (128, 512, 2048, 8192)

    def __init__(
        self,
        repository: AssetProcessingDocuments,
        paths: RuntimePaths | None = None,
    ):
        self.repository = repository
        self.paths = paths or RuntimePaths.discover()

    def generate(
        self,
        asset: Asset,
        *,
        duration_seconds: float,
        progress: Callable[[OperationProgress], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Asset:
        source = self.repository.resolve_asset_path(asset)
        output_dir = self.repository.project_dir / "cache" / "waveforms"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{asset.id}.json"
        decoded = output_dir / f"{asset.id}.pcm.tmp"
        if duration_seconds <= 0:
            if progress:
                progress(OperationProgress.indeterminate("waveform_decoding"))
            observer = None
        else:
            observer = FfmpegProgressObserver(
                duration_seconds,
                lambda position: progress(
                    OperationProgress.determinate(
                        "waveform_decoding",
                        completed=position,
                        total=duration_seconds,
                        unit="media_seconds",
                    )
                )
                if progress
                else None,
            )
        try:
            result = run_cancellable_streaming(
                ffmpeg_progress_command(
                    [
                        str(self.paths.ffmpeg),
                        "-y",
                        "-hide_banner",
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
                    ]
                ),
                on_stderr_line=observer,
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
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"sample_rate": self.SAMPLE_RATE, "sample_count": len(samples), "levels": levels},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(output)
        return self.repository.set_asset_waveform_path(
            asset.id,
            expected_fingerprint=asset.fingerprint,
            waveform_path=output,
        )

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
