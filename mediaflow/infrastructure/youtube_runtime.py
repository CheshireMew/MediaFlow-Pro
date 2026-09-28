from __future__ import annotations

import shutil
from typing import Any

from .runtime_paths import RuntimePaths


def youtube_runtime_options(paths: RuntimePaths) -> dict[str, Any]:
    """Enable installed JS engines for both analysis and downloading.

    Node is not enabled by yt-dlp by default, even when it is on PATH.
    EJS is installed with yt-dlp's default dependencies and by our updater.
    """
    runtimes = {name: {"path": executable} for name in ("deno", "node") if (executable := shutil.which(name))}
    return {
        "js_runtimes": runtimes,
        "cachedir": str(paths.runtime_dir / "cache" / "yt-dlp"),
    }
