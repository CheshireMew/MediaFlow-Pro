from __future__ import annotations

import re
import subprocess

from mediaflow.infrastructure.encoder_catalog import VIDEO_ENCODERS
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.runtime_paths import RuntimePaths


class EncoderDiscoveryService:
    def __init__(self, paths: RuntimePaths | None = None):
        self.paths = paths or RuntimePaths.discover()
        bundled_ffmpeg = self.paths.melt.parent / "ffmpeg.exe" if self.paths.melt else None
        executable = (
            bundled_ffmpeg if bundled_ffmpeg is not None and bundled_ffmpeg.is_file() else self.paths.ffmpeg
        )
        self.ffmpeg = FfmpegRunner(executable)

    def video_options(self) -> list[dict]:
        available = self._available_encoders()
        return [
            {
                "labelKey": spec.label_key,
                "value": name,
                "formats": [spec.format.value],
            }
            for name, spec in VIDEO_ENCODERS.items()
            if name in available and (not spec.hardware or self._encoder_works(name))
        ]

    def _available_encoders(self) -> set[str]:
        try:
            result = self.ffmpeg.run(
                ["-encoders"],
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return set()
        if result.returncode != 0:
            return set()
        return {
            match.group(1)
            for line in result.stdout.splitlines()
            if (match := re.match(r"^\s*[A-Z\.]{6}\s+(\S+)", line))
        }

    def _encoder_works(self, name: str) -> bool:
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
                    "NUL",
                ],
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0
