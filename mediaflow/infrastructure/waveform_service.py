from __future__ import annotations

import json
from array import array
from collections.abc import Callable

from mediaflow.application.ports import AssetProcessingDocuments
from mediaflow.domain.project import Asset

from .runtime_paths import RuntimePaths
from .subprocess_runner import run_cancellable


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
        check_cancelled: Callable[[], None] | None = None,
    ) -> Asset:
        source = self.repository.resolve_asset_path(asset)
        result = run_cancellable(
            [
                str(self.paths.ffmpeg),
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
                "pipe:1",
            ],
            timeout=300,
            check_cancelled=check_cancelled,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Waveform decode failed: {result.stderr.decode(errors='replace')}")
        samples = array("h")
        samples.frombytes(result.stdout)
        levels = {str(block): self._peaks(samples, block) for block in self.BLOCK_SIZES}
        output_dir = self.repository.project_dir / "cache" / "waveforms"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{asset.id}.json"
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
    def _peaks(samples: array, block_size: int) -> list[list[float]]:
        peaks: list[list[float]] = []
        scale = 1.0 / 32768.0
        for offset in range(0, len(samples), block_size):
            block = samples[offset : offset + block_size]
            if not block:
                continue
            peaks.append([min(block) * scale, max(block) * scale])
        return peaks
