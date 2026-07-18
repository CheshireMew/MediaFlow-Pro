from __future__ import annotations

import re
from pathlib import Path

from mediaflow.domain.enums import AssetKind

VIDEO_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".mts",
)
AUDIO_EXTENSIONS = (
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".m4a",
    ".wma",
    ".opus",
)
MEDIA_EXTENSIONS = (*VIDEO_EXTENSIONS, *AUDIO_EXTENSIONS)

_TRANSLATION_SUFFIXES = (
    "_zh-cn",
    "_zh-tw",
    "_en",
    "_jp",
    "_ja",
    "_es",
    "_fr",
    "_de",
    "_ru",
    "_ko",
    ".zh-cn",
    ".zh-tw",
    ".en",
    ".jp",
    ".ja",
    ".es",
    ".fr",
    ".de",
    ".ru",
    ".ko",
)


def related_media_stem(path: str | Path) -> str:
    """Return the stable stem shared by a subtitle and its source media."""
    stem = Path(path).stem.casefold()
    for suffix in _TRANSLATION_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    for extension in VIDEO_EXTENSIONS:
        if stem.endswith(extension):
            stem = stem[: -len(extension)]
            break
    return re.sub(r"[\s._-]+", " ", stem).strip()


def related_media_kind(extension: str) -> AssetKind | None:
    normalized = extension.casefold()
    if normalized in VIDEO_EXTENSIONS:
        return AssetKind.VIDEO
    if normalized in AUDIO_EXTENSIONS:
        return AssetKind.AUDIO
    return None


def related_media_paths(subtitle_path: str | Path) -> list[Path]:
    """Build adjacent same-name media candidates in the original app's order."""
    source = Path(subtitle_path)
    base = source.with_suffix("")
    raw_base = str(base)
    lower_base = raw_base.casefold()
    for suffix in _TRANSLATION_SUFFIXES:
        if lower_base.endswith(suffix):
            raw_base = raw_base[: -len(suffix)]
            lower_base = lower_base[: -len(suffix)]
            break
    embedded_extension = next(
        (extension for extension in VIDEO_EXTENSIONS if lower_base.endswith(extension)),
        None,
    )
    if embedded_extension:
        media_stem = Path(raw_base[: -len(embedded_extension)])
        candidates = [Path(raw_base)]
    else:
        media_stem = Path(raw_base)
        candidates = []
    raw = str(media_stem)
    candidates.extend(Path(f"{raw}{extension}") for extension in MEDIA_EXTENSIONS)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique
