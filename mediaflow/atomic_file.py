from __future__ import annotations

import os
import uuid
from pathlib import Path

from mediaflow.domain.storage_names import require_windows_interop_path


def unique_temporary_sibling(
    destination: str | Path,
    *,
    label: str = "writing",
) -> Path:
    """Return a collision-free temporary path beside its atomic destination.

    The destination suffix remains the final suffix so tools such as FFmpeg can
    infer the intended container from the temporary file name.
    """

    path = Path(destination)
    normalized_label = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in label.strip()
    ).strip("-")[:12]
    if not normalized_label:
        raise ValueError("Temporary file labels cannot be empty")
    return path.with_name(
        f".mf-{normalized_label}-{os.getpid():x}-{uuid.uuid4().hex[:12]}.tmp{path.suffix}"
    )


def native_temporary_sibling(
    destination: str | Path,
    *,
    label: str = "writing",
) -> Path:
    """Return a unique sibling that is safe to pass to Windows media tools."""

    return require_windows_interop_path(
        unique_temporary_sibling(destination, label=label)
    )


def atomic_write_text(
    destination: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    durable: bool = False,
    mode: int | None = None,
) -> Path:
    """Write text without exposing a partial destination to concurrent readers."""

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = unique_temporary_sibling(path)
    try:
        with temporary.open("x", encoding=encoding, newline="\n") as stream:
            stream.write(content)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def atomic_write_bytes(
    destination: str | Path,
    content: bytes,
    *,
    durable: bool = False,
    mode: int | None = None,
) -> Path:
    """Write bytes without exposing a partial destination to concurrent readers."""

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = unique_temporary_sibling(path)
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
