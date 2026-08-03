from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

HashProgress = Callable[[int, int], None]


def sha256_file(
    path: str | Path,
    *,
    chunk_size: int = 4 * 1024 * 1024,
    progress: HashProgress | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> str:
    """Return a full-file SHA-256 digest through the shared streaming boundary."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    source_path = Path(path)
    total = source_path.stat().st_size
    completed = 0
    digest = hashlib.sha256()
    with source_path.open("rb") as source:
        while chunk := source.read(chunk_size):
            if check_cancelled is not None:
                check_cancelled()
            digest.update(chunk)
            completed += len(chunk)
            if progress is not None:
                progress(completed, total)
    return digest.hexdigest()
