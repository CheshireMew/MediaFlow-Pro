from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from mediaflow.infrastructure.encoder_catalog import VIDEO_ENCODERS
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.runtime_paths import RuntimePaths

_BACKEND_VENDOR = {
    "nvenc": "nvidia",
    "qsv": "intel",
    "amf": "amd",
    "videotoolbox": "apple",
}


class EncoderDiscoveryService:
    def __init__(self, paths: RuntimePaths):
        self.paths = paths
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)
        self._available_cache: frozenset[str] | None = None
        self._working_cache: dict[str, bool] = {}

    def video_options(self) -> list[dict]:
        available = self._available_encoders()
        options: list[dict] = []
        observed_software: set[str] = set()
        working_hardware: dict[str, set[str]] = {}
        hardware_candidates = [
            (name, spec)
            for name, spec in VIDEO_ENCODERS.items()
            if name in available and spec.hardware
        ]
        if hardware_candidates:
            with ThreadPoolExecutor(
                max_workers=min(8, len(hardware_candidates)),
                thread_name_prefix="mediaflow-encoder-probe",
            ) as executor:
                hardware_results = dict(
                    zip(
                        (name for name, _spec in hardware_candidates),
                        executor.map(
                            self.encoder_works,
                            (name for name, _spec in hardware_candidates),
                        ),
                        strict=True,
                    )
                )
        else:
            hardware_results = {}
        for name, spec in VIDEO_ENCODERS.items():
            if name not in available or (spec.hardware and not hardware_results[name]):
                continue
            format_name = spec.format.value
            if spec.backend != "software":
                working_hardware.setdefault(format_name, set()).add(spec.backend)
                continue
            if format_name in observed_software:
                continue
            observed_software.add(format_name)
            options.append(
                {
                    "labelKey": spec.label_key,
                    "value": "software",
                    "mode": "software",
                    "vendor": "auto",
                    "formats": [format_name],
                }
            )
        for format_name, backends in working_hardware.items():
            vendors = {vendor for backend in backends if (vendor := _BACKEND_VENDOR.get(backend))}
            for vendor in ("auto", "nvidia", "intel", "amd", "apple"):
                if vendor != "auto" and vendor not in vendors:
                    continue
                options.append(
                    {
                        "labelKey": f"{format_name}_hardware_{vendor}",
                        "value": f"prefer_hardware:{vendor}",
                        "mode": "prefer_hardware",
                        "vendor": vendor,
                        "formats": [format_name],
                    }
                )
        return options

    def _available_encoders(self) -> set[str]:
        if self._available_cache is not None:
            return set(self._available_cache)
        try:
            result = self.ffmpeg.run(
                ["-encoders"],
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return set()
        if result.returncode != 0:
            return set()
        available = {
            match.group(1)
            for line in result.stdout.splitlines()
            if (match := re.match(r"^\s*[A-Z\.]{6}\s+(\S+)", line))
        }
        self._available_cache = frozenset(available)
        return available

    def encoder_works(self, name: str) -> bool:
        cached = self._working_cache.get(name)
        if cached is not None:
            return cached
        if name not in self._available_encoders():
            self._working_cache[name] = False
            return False
        try:
            result = self.ffmpeg.run(
                [
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=size=320x180:rate=30",
                    "-vf",
                    "format=yuv420p",
                    "-frames:v",
                    "1",
                    "-c:v",
                    name,
                    "-f",
                    "null",
                    os.devnull,
                ],
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            works = False
        else:
            works = result.returncode == 0
        self._working_cache[name] = works
        return works
