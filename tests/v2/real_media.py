from __future__ import annotations

import subprocess
from pathlib import Path

from mediaflow.infrastructure.runtime_paths import RuntimePaths


def generate_real_media(
    path: Path,
    paths: RuntimePaths,
    *,
    width: int = 640,
    height: int = 360,
) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=25:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to generate real media: {result.stderr}")
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Real media generator produced no usable file: {path}")
