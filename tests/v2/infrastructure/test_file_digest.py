from __future__ import annotations

import hashlib

import pytest

from mediaflow.file_digest import sha256_file


def test_sha256_file_streams_one_canonical_digest_with_progress(tmp_path) -> None:
    source = tmp_path / "payload.bin"
    payload = bytes(range(251)) * 41
    source.write_bytes(payload)
    progress: list[tuple[int, int]] = []

    actual = sha256_file(
        source,
        chunk_size=1024,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert actual == hashlib.sha256(payload).hexdigest()
    assert progress[-1] == (len(payload), len(payload))
    assert all(
        left[0] < right[0]
        for left, right in zip(progress, progress[1:], strict=False)
    )


def test_sha256_file_checks_cancellation_before_publishing_chunk_progress(
    tmp_path,
) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(b"x" * 4096)
    progress: list[tuple[int, int]] = []

    with pytest.raises(RuntimeError, match="cancelled"):
        sha256_file(
            source,
            chunk_size=1024,
            progress=lambda completed, total: progress.append((completed, total)),
            check_cancelled=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")),
        )

    assert progress == []
