from __future__ import annotations

import hashlib
from pathlib import Path

from mediaflow.domain.models import AssetFingerprint

EDGE_CHUNK_SIZE = 1024 * 1024


def fingerprint_file(path: str | Path) -> AssetFingerprint:
    source = Path(path).resolve(strict=True)
    stat = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        digest.update(stream.read(EDGE_CHUNK_SIZE))
        if stat.st_size > EDGE_CHUNK_SIZE:
            stream.seek(max(0, stat.st_size - EDGE_CHUNK_SIZE))
            digest.update(stream.read(EDGE_CHUNK_SIZE))
    digest.update(str(stat.st_size).encode("ascii"))
    return AssetFingerprint(
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        edge_sha256=digest.hexdigest(),
    )


def fingerprint_matches(path: str | Path, expected: AssetFingerprint) -> bool:
    try:
        actual = fingerprint_file(path)
    except (FileNotFoundError, OSError):
        return False
    return actual.size == expected.size and actual.edge_sha256 == expected.edge_sha256
