from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath

_MEDIA_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4a": "audio/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".webp": "image/webp",
}


def media_mime_type(file_name: str) -> str | None:
    """Resolve supported media types without consulting machine MIME registries."""

    clean_name = file_name.split("#", 1)[0]
    suffix = Path(clean_name).suffix.casefold()
    return (
        _MEDIA_MIME_TYPES.get(suffix)
        or mimetypes.guess_type(
            clean_name,
            strict=False,
        )[0]
    )


def local_package_path(value: str) -> str:
    normalized = value.strip()
    if "\\" in normalized:
        raise ValueError("Editable media paths must use /")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("Editable media paths must stay inside the package")
    if ":" in path.parts[0] or "://" in normalized:
        raise ValueError("Editable media paths cannot use a URL or drive protocol")
    return path.as_posix()


def local_media_reference(value: str) -> str:
    reference = value.strip().replace("\\", "/")
    file_part, separator, fragment = reference.partition("#")
    normalized = local_package_path(file_part)
    if separator and not fragment:
        raise ValueError("Editable media references cannot end with an empty fragment")
    return f"{normalized}#{fragment}" if separator else normalized
