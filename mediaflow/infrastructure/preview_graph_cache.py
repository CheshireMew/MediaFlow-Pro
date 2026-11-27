from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.storage_names import content_addressed_child_path
from mediaflow.infrastructure.project_cache_budget import reserve_project_cache
from mediaflow.infrastructure.runtime_paths import RuntimePaths

PREVIEW_GRAPH_CACHE_MAX_BYTES = 256 * 1024**2
PREVIEW_GRAPH_CACHE_KEEP_FILES = 16


class PreviewGraphCache:
    """Bound derived XML while retaining each connected preview's current graph."""

    def __init__(self, project_dir: Path, paths: RuntimePaths) -> None:
        self.project_dir, self.paths = project_dir, paths
        self.root = paths.project_cache_dir(project_dir)
        self._lock = threading.RLock()
        self._pins: dict[tuple[str, str], Path] = {}

    def release(self, owner: str | None) -> bool:
        with self._lock:
            for key in tuple(self._pins):
                if owner is None or key[0] == owner:
                    self._pins.pop(key)
            return not self._pins

    @contextmanager
    def _publication_scope(self, check_cancelled: Callable[[], None] | None) -> Iterator[None]:
        while not self._lock.acquire(timeout=0.05):
            if check_cancelled is not None:
                check_cancelled()
        try:
            if check_cancelled is not None:
                check_cancelled()
            yield
        finally:
            self._lock.release()

    def publish(
        self,
        xml: str,
        *,
        namespace: str,
        owner: str = "direct",
        surface: Literal["timeline", "source"] = "timeline",
        check_cancelled: Callable[[], None] | None = None,
    ) -> Path:
        destination = content_addressed_child_path(
            self.root / "mlt",
            xml,
            namespace=namespace,
            suffix=".mlt",
        )
        size = len(xml.encode("utf-8"))
        with self._publication_scope(check_cancelled):
            protected = set(self._pins.values()) | {destination}
            entries = [
                path
                for path in destination.parent.glob("*.mlt")
                if path.name.startswith(("pv-", "source-")) and path.is_file()
            ]
            total = sum(path.stat().st_size for path in entries)
            growth = 0 if destination.is_file() else size
            pinned_bytes = sum(path.stat().st_size for path in protected if path.is_file()) + growth
            if pinned_bytes > PREVIEW_GRAPH_CACHE_MAX_BYTES:
                raise RuntimeError("活动预览图已达到缓存上限，请关闭不再使用的预览")
            for path in sorted(entries, key=lambda item: item.stat().st_mtime_ns):
                if (
                    total + growth <= PREVIEW_GRAPH_CACHE_MAX_BYTES
                    and len(entries) + bool(growth) <= PREVIEW_GRAPH_CACHE_KEEP_FILES
                ):
                    break
                if path not in protected:
                    total -= path.stat().st_size
                    path.unlink()
                    entries.remove(path)
            if growth:
                with reserve_project_cache(
                    self.root,
                    self.project_dir,
                    expected_new_bytes=size,
                    label="Preview graph",
                    case_sensitive_paths=self.paths.target.case_sensitive_paths,
                    check_cancelled=check_cancelled,
                ):
                    if check_cancelled is not None:
                        check_cancelled()
                    atomic_write_text(destination, xml)
            os.utime(destination, None)
            self._pins[(owner, surface)] = destination
        return destination
