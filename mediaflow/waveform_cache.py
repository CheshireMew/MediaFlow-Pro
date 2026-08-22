from __future__ import annotations

import struct
import sys
from array import array
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

WAVEFORM_CACHE_VERSION = 3
WAVEFORM_CACHE_SUFFIX = ".mfwave"
_MAGIC = b"MFWAVE3\0"
_HEADER = struct.Struct("<8sIIQI")
_LEVEL = struct.Struct("<IQQ")
_PEAK_BYTES = 4


@dataclass(frozen=True, slots=True)
class WaveformLevel:
    block_size: int
    count: int
    offset: int


@dataclass(frozen=True, slots=True)
class WaveformCacheHeader:
    sample_rate: int
    sample_count: int
    levels: tuple[WaveformLevel, ...]

    def as_descriptor(self, path: Path) -> dict[str, object]:
        return {
            "schema": "mediaflow-waveform-cache/v3",
            "sample_rate": self.sample_rate,
            "sample_count": self.sample_count,
            "path": str(path.resolve()),
            "levels": {
                str(level.block_size): {
                    "count": level.count,
                    "offset": level.offset,
                }
                for level in self.levels
            },
        }


def inspect_waveform_cache(path: Path) -> WaveformCacheHeader:
    resolved = path.resolve()
    with resolved.open("rb") as stream:
        payload = stream.read(_HEADER.size)
        if len(payload) != _HEADER.size:
            raise ValueError("Waveform cache header is incomplete")
        magic, version, sample_rate, sample_count, level_count = _HEADER.unpack(payload)
        if magic != _MAGIC or version != WAVEFORM_CACHE_VERSION:
            raise ValueError("Waveform cache version is not supported")
        if sample_rate <= 0 or sample_count < 0 or level_count <= 0:
            raise ValueError("Waveform cache metadata is invalid")
        file_size = resolved.stat().st_size
        minimum_offset = _HEADER.size + level_count * _LEVEL.size
        levels: list[WaveformLevel] = []
        previous_block = 0
        previous_end = minimum_offset
        for _index in range(level_count):
            entry = stream.read(_LEVEL.size)
            if len(entry) != _LEVEL.size:
                raise ValueError("Waveform cache level directory is incomplete")
            block_size, count, offset = _LEVEL.unpack(entry)
            end = offset + count * _PEAK_BYTES
            if (
                block_size <= previous_block
                or count <= 0
                or offset < previous_end
                or end > file_size
            ):
                raise ValueError("Waveform cache level directory is invalid")
            levels.append(WaveformLevel(block_size, count, offset))
            previous_block = block_size
            previous_end = end
        if previous_end != file_size:
            raise ValueError("Waveform cache contains untracked trailing data")
    return WaveformCacheHeader(sample_rate, sample_count, tuple(levels))


def waveform_cache_is_current(path: Path) -> bool:
    try:
        inspect_waveform_cache(path)
    except (OSError, ValueError):
        return False
    return True


def waveform_cache_size[Key](level_counts: Mapping[Key, int]) -> int:
    return _HEADER.size + len(level_counts) * _LEVEL.size + sum(
        int(count) * _PEAK_BYTES for count in level_counts.values()
    )


def read_waveform_peaks(
    path: Path,
    *,
    offset: int,
    count: int,
    first: int,
    last: int,
) -> list[tuple[float, float]]:
    if offset < 0 or count < 0 or first < 0 or last < first or last > count:
        raise ValueError("Waveform peak range is invalid")
    if first == last:
        return []
    with path.open("rb") as stream:
        stream.seek(offset + first * _PEAK_BYTES)
        payload = stream.read((last - first) * _PEAK_BYTES)
    if len(payload) != (last - first) * _PEAK_BYTES:
        raise ValueError("Waveform peak range is incomplete")
    values = array("h")
    values.frombytes(payload)
    if sys.byteorder != "little":
        values.byteswap()
    scale = 1.0 / 32768.0
    return [
        (values[index] * scale, values[index + 1] * scale)
        for index in range(0, len(values), 2)
    ]


def write_waveform_cache(
    output: Path,
    *,
    sample_rate: int,
    sample_count: int,
    fragments: Mapping[int, Path],
    level_counts: Mapping[int, int],
) -> None:
    block_sizes = tuple(sorted(fragments))
    if not block_sizes or set(block_sizes) != set(level_counts):
        raise ValueError("Waveform cache levels are incomplete")
    header_size = _HEADER.size + len(block_sizes) * _LEVEL.size
    offsets: dict[int, int] = {}
    offset = header_size
    for block_size in block_sizes:
        count = int(level_counts[block_size])
        fragment_size = fragments[block_size].stat().st_size
        if count <= 0 or fragment_size != count * _PEAK_BYTES:
            raise ValueError("Waveform cache fragment size does not match its peak count")
        offsets[block_size] = offset
        offset += fragment_size

    with output.open("xb") as stream:
        stream.write(
            _HEADER.pack(
                _MAGIC,
                WAVEFORM_CACHE_VERSION,
                sample_rate,
                sample_count,
                len(block_sizes),
            )
        )
        for block_size in block_sizes:
            stream.write(
                _LEVEL.pack(
                    block_size,
                    int(level_counts[block_size]),
                    offsets[block_size],
                )
            )
        for block_size in block_sizes:
            with fragments[block_size].open("rb") as fragment:
                while chunk := fragment.read(1024 * 1024):
                    stream.write(chunk)
        stream.flush()
