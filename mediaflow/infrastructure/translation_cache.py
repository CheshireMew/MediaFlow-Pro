from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any


class TranslationCache:
    """Project-local cache for validated, timing-preserving translation batches."""

    SCHEMA_VERSION = 1
    MAX_AGE_SECONDS = 7 * 24 * 60 * 60

    def __init__(self, project_dir: Path):
        self.directory = project_dir / "cache" / "translations"
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, request: dict[str, Any]) -> list[str] | None:
        path = self._path(request)
        if not path.is_file():
            return None
        try:
            if time.time() - path.stat().st_mtime > self.MAX_AGE_SECONDS:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        values = payload.get("texts") if isinstance(payload, dict) else None
        if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
            return None
        return values

    def put(self, request: dict[str, Any], texts: list[str]) -> None:
        if not texts or not all(texts):
            return
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "created_at": int(time.time()),
            "texts": texts,
        }
        destination = self._path(request)
        temporary = destination.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(destination)

    def _path(self, request: dict[str, Any]) -> Path:
        canonical = json.dumps(
            {
                "schema_version": self.SCHEMA_VERSION,
                **request,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        return self.directory / f"{digest}.json"
