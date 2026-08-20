from __future__ import annotations

import json
from pathlib import Path

from mediaflow.domain.portable_timeline import (
    LoadedPortableTimeline,
    PortableTimelineDocument,
)
from mediaflow.file_digest import sha256_file


def _resolve_relative_file(root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(
            f"Portable source must stay inside the timeline directory: {relative}"
        )
    target = (root / value).resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"Portable source must stay inside the timeline directory: {relative}"
        ) from error
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def load_portable_timeline(path: str | Path) -> LoadedPortableTimeline:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Portable timeline is not valid JSON: {source}") from error
    document = PortableTimelineDocument.model_validate(raw)
    root = source.parent.resolve()
    sources: dict[str, Path] = {}
    for item in document.sources:
        resolved = _resolve_relative_file(root, item.file)
        if sha256_file(resolved) != item.sha256:
            raise ValueError(f"Portable source hash does not match: {item.id}")
        sources[item.id] = resolved
    return LoadedPortableTimeline(
        path=source,
        root=root,
        sha256=sha256_file(source),
        document=document,
        sources=sources,
    )
