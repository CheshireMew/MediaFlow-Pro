from __future__ import annotations

import re
import subprocess

from mediaflow.infrastructure.runtime_paths import RuntimePaths

_VIDEO_ENCODERS = {
    "libx264": ("h264_software", ["h264"]),
    "h264_nvenc": ("h264_nvidia", ["h264"]),
    "h264_qsv": ("h264_intel_qsv", ["h264"]),
    "h264_amf": ("h264_amd_amf", ["h264"]),
    "libx265": ("hevc_software", ["hevc"]),
    "hevc_nvenc": ("hevc_nvidia", ["hevc"]),
    "hevc_qsv": ("hevc_intel_qsv", ["hevc"]),
    "hevc_amf": ("hevc_amd_amf", ["hevc"]),
    "libsvtav1": ("av1_svt_software", ["av1"]),
    "av1_nvenc": ("av1_nvidia", ["av1"]),
    "av1_qsv": ("av1_intel_qsv", ["av1"]),
    "av1_amf": ("av1_amd_amf", ["av1"]),
    "prores_ks": ("prores_software", ["prores"]),
}


class EncoderDiscoveryService:
    def __init__(self, paths: RuntimePaths | None = None):
        self.paths = paths or RuntimePaths.discover()
        bundled_ffmpeg = self.paths.melt.parent / "ffmpeg.exe" if self.paths.melt else None
        self.ffmpeg = (
            bundled_ffmpeg
            if bundled_ffmpeg is not None and bundled_ffmpeg.is_file()
            else self.paths.ffmpeg
        )

    def video_options(self) -> list[dict]:
        available = self._available_encoders()
        return [
            {"labelKey": label_key, "value": name, "formats": formats}
            for name, (label_key, formats) in _VIDEO_ENCODERS.items()
            if name in available and ("_" not in name or self._encoder_works(name))
        ]

    def _available_encoders(self) -> set[str]:
        try:
            result = subprocess.run(
                [str(self.ffmpeg), "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
        if name in {"libx264", "libx265", "libsvtav1", "prores_ks"}:
            return True
        try:
            result = subprocess.run(
                [
                    str(self.ffmpeg),
                    "-hide_banner",
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
                capture_output=True,
                timeout=15,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0
