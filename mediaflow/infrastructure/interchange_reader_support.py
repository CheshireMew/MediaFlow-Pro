from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlparse

from mediaflow.domain.portable_timeline import (
    PortableCaptionClip,
    PortableMediaClip,
)


def require_non_overlapping(
    clips: Sequence[PortableMediaClip | PortableCaptionClip],
    track_id: str,
) -> None:
    previous_end = 0.0
    for clip in sorted(clips, key=lambda item: (item.timeline_start_seconds, item.id)):
        if clip.timeline_start_seconds < previous_end - 1e-9:
            raise ValueError(f"交换时间线轨道 {track_id} 包含重叠片段，无法映射为单一原生轨道")
        previous_end = clip.timeline_start_seconds + clip.duration_seconds


def resolve_media_path(raw: str, root: Path) -> Path | None:
    value = raw.strip()
    if not value:
        return None
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return Path(value).resolve()
    parsed = urlparse(value)
    if parsed.scheme.casefold() == "file":
        decoded = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", decoded):
            decoded = decoded[1:]
        candidate = Path(decoded)
    elif parsed.scheme:
        return None
    else:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
    try:
        return candidate.resolve()
    except OSError:
        return candidate.absolute()
