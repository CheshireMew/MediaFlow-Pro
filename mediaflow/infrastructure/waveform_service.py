from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from mediaflow.application.ports import AssetProcessingDocuments
from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset
from mediaflow.domain.storage_names import require_windows_interop_path
from mediaflow.waveform_cache import (
    WAVEFORM_CACHE_SUFFIX,
    WAVEFORM_CACHE_VERSION,
    waveform_cache_size,
    write_waveform_cache,
)

from .ffmpeg_runner import FfmpegRunner
from .runtime_paths import RuntimePaths
from .storage_budget import reserve_project_cache


class WaveformService:
    CACHE_VERSION = WAVEFORM_CACHE_VERSION
    SAMPLE_RATE = 8_000
    BLOCK_SIZES = (128, 512, 2048, 8192)

    def __init__(
        self,
        repository: AssetProcessingDocuments,
        paths: RuntimePaths,
    ):
        self.repository = repository
        self.paths = paths
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)

    def prepare(
        self,
        asset: Asset,
        *,
        duration_seconds: float,
        progress: Callable[[OperationProgress], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Path:
        source = self.repository.assets.resolve_asset_path(asset)
        if not source.is_file():
            raise FileNotFoundError(source)
        source = require_windows_interop_path(source)
        project_cache = self.paths.project_cache_dir(self.repository.project_dir)
        reserve_project_cache(
            project_cache,
            self.repository.project_dir,
            expected_new_bytes=self._estimated_peak_bytes(duration_seconds),
            label="MediaFlow waveform cache",
            case_sensitive_paths=self.paths.target.case_sensitive_paths,
        )
        output_dir = project_cache / "waveforms"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{asset.id}-{self._cache_key(asset, source)}{WAVEFORM_CACHE_SUFFIX}"
        fragments = {
            block_size: unique_temporary_sibling(
                output_dir / f"{asset.id}-{block_size}.peaks",
                label="waveform",
            )
            for block_size in self.BLOCK_SIZES
        }
        temporary_output = unique_temporary_sibling(output, label="waveform")
        pipe = None
        try:
            if progress:
                progress(OperationProgress.indeterminate("waveform_decoding"))
            pipe = self.ffmpeg.open_output_pipe(
                [
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
                ]
            )
            import numpy as np

            with ExitStack() as stack:
                fragment_streams = {
                    block_size: stack.enter_context(path.open("xb"))
                    for block_size, path in fragments.items()
                }
                level_counts = {block_size: 0 for block_size in self.BLOCK_SIZES}
                pending_peaks = {
                    level_index: np.empty((0, 2), dtype="<i2")
                    for level_index in range(len(self.BLOCK_SIZES) - 1)
                }
                base_carry = np.empty(0, dtype="<i2")

                def write_level(level_index: int, peaks) -> None:
                    if not len(peaks):
                        return
                    block_size = self.BLOCK_SIZES[level_index]
                    encoded = np.ascontiguousarray(peaks, dtype="<i2")
                    fragment_streams[block_size].write(encoded.tobytes(order="C"))
                    level_counts[block_size] += int(encoded.shape[0])
                    if level_index + 1 >= len(self.BLOCK_SIZES):
                        return
                    next_size = self.BLOCK_SIZES[level_index + 1]
                    ratio = next_size // block_size
                    pending = pending_peaks[level_index]
                    combined = np.concatenate((pending, encoded)) if len(pending) else encoded
                    complete_count = len(combined) - len(combined) % ratio
                    pending_peaks[level_index] = combined[complete_count:].copy()
                    if not complete_count:
                        return
                    groups = combined[:complete_count].reshape(-1, ratio, 2)
                    aggregate = np.empty((groups.shape[0], 2), dtype="<i2")
                    aggregate[:, 0] = groups[:, :, 0].min(axis=1)
                    aggregate[:, 1] = groups[:, :, 1].max(axis=1)
                    write_level(level_index + 1, aggregate)

                sample_count = 0
                remainder = b""
                while chunk := pipe.read(512 * 1024):
                    if check_cancelled is not None:
                        check_cancelled()
                    payload = remainder + chunk
                    even_length = len(payload) - len(payload) % 2
                    remainder = payload[even_length:]
                    samples = np.frombuffer(payload[:even_length], dtype="<i2")
                    sample_count += int(samples.size)
                    combined = (
                        np.concatenate((base_carry, samples))
                        if base_carry.size
                        else samples
                    )
                    base_size = self.BLOCK_SIZES[0]
                    complete_length = combined.size - combined.size % base_size
                    if complete_length:
                        groups = combined[:complete_length].reshape(-1, base_size)
                        peaks = np.empty((groups.shape[0], 2), dtype="<i2")
                        peaks[:, 0] = groups.min(axis=1)
                        peaks[:, 1] = groups.max(axis=1)
                        write_level(0, peaks)
                    base_carry = combined[complete_length:].copy()
                    if progress and duration_seconds > 0:
                        progress(
                            OperationProgress.determinate(
                                "waveform_decoding",
                                completed=min(
                                    duration_seconds,
                                    sample_count / self.SAMPLE_RATE,
                                ),
                                total=duration_seconds,
                                unit="media_seconds",
                            )
                        )
                if remainder:
                    raise RuntimeError("Waveform decoder returned an incomplete PCM sample")
                result = pipe.finish(timeout=300)
                pipe = None
                if result.returncode != 0:
                    raise RuntimeError(f"Waveform decode failed: {result.stderr}")
                if progress:
                    decoded_seconds = sample_count / self.SAMPLE_RATE
                    progress(
                        OperationProgress.determinate(
                            "waveform_decoding",
                            completed=(duration_seconds if duration_seconds > 0 else decoded_seconds),
                            total=(duration_seconds if duration_seconds > 0 else max(decoded_seconds, 1e-9)),
                            unit="media_seconds",
                        )
                    )
                if sample_count <= 0:
                    raise RuntimeError("Waveform decoder returned no PCM samples")
                if base_carry.size:
                    write_level(
                        0,
                        np.asarray(
                            [[base_carry.min(), base_carry.max()]],
                            dtype="<i2",
                        ),
                    )
                for level_index in range(len(self.BLOCK_SIZES) - 1):
                    pending = pending_peaks[level_index]
                    if not len(pending):
                        continue
                    aggregate = np.asarray(
                        [[pending[:, 0].min(), pending[:, 1].max()]],
                        dtype="<i2",
                    )
                    pending_peaks[level_index] = np.empty((0, 2), dtype="<i2")
                    write_level(level_index + 1, aggregate)
                expected_counts = {
                    block_size: (sample_count + block_size - 1) // block_size
                    for block_size in self.BLOCK_SIZES
                }
                if level_counts != expected_counts:
                    raise RuntimeError("Waveform pyramid counts do not match decoded samples")
                if progress:
                    actual_work = max(1, sample_count)
                    progress(
                        OperationProgress.determinate(
                            "waveform_calculating",
                            completed=actual_work,
                            total=actual_work,
                            unit="samples",
                        )
                    )
                    progress(OperationProgress.indeterminate("waveform_saving"))

            write_waveform_cache(
                temporary_output,
                sample_rate=self.SAMPLE_RATE,
                sample_count=sample_count,
                fragments=fragments,
                level_counts=level_counts,
            )
            temporary_output.replace(output)
            return output
        finally:
            if pipe is not None:
                pipe.abort()
            temporary_output.unlink(missing_ok=True)
            for path in fragments.values():
                path.unlink(missing_ok=True)

    def generate(
        self,
        asset: Asset,
        *,
        duration_seconds: float,
        progress: Callable[[OperationProgress], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Asset:
        output = self.prepare(
            asset,
            duration_seconds=duration_seconds,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        return self.repository.assets.set_asset_waveform_path(
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

    @classmethod
    def _estimated_peak_bytes(cls, duration_seconds: float) -> int:
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError(
                "Waveform storage preflight requires a known positive media duration"
            )
        # Keep one second of decoder/metadata tolerance. During publication the
        # four peak fragments and the assembled cache coexist, so reserve both
        # complete payloads before creating either one.
        sample_count = math.ceil(duration_seconds * cls.SAMPLE_RATE) + cls.SAMPLE_RATE
        level_counts = {
            block_size: (sample_count + block_size - 1) // block_size
            for block_size in cls.BLOCK_SIZES
        }
        return waveform_cache_size(level_counts) * 2
