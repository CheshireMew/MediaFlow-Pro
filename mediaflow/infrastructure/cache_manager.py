from __future__ import annotations

import json
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure.process_liveness import process_is_alive


@dataclass(frozen=True, slots=True)
class _RunManifest:
    owner_pid: int
    created_at_ns: int


class CacheManager:
    """Own temporary run directories and bounded derived-cache retention."""

    RUN_MANIFEST = ".mediaflow-run.json"
    RUN_SCHEMA_VERSION = 1

    def __init__(self, cache_root: str | Path):
        self.root = Path(cache_root).resolve()

    def create_run(self, category: str) -> Path:
        normalized = category.strip().replace("\\", "/").strip("/")
        category_path = Path(normalized)
        if (
            not normalized
            or category_path.is_absolute()
            or bool(category_path.drive)
            or any(part in {".", ".."} for part in category_path.parts)
        ):
            raise ValueError("Cache category must stay inside the cache root")
        run_id = str(uuid.uuid4())
        run = self.root / category_path / "runs" / run_id
        self._require_managed(run.resolve())
        run.mkdir(parents=True, exist_ok=False)
        try:
            atomic_write_text(
                run / self.RUN_MANIFEST,
                json.dumps(
                    {
                        "schema_version": self.RUN_SCHEMA_VERSION,
                        "owner_pid": os.getpid(),
                        "created_at_ns": time.time_ns(),
                    },
                    separators=(",", ":"),
                ),
            )
        except BaseException as error:
            archive = (
                self.root
                / "archive"
                / "failed-run-creation"
                / f"{category_path.as_posix().replace('/', '-')}-{run_id}"
            )
            try:
                archive.parent.mkdir(parents=True, exist_ok=True)
                run.replace(archive)
                error.add_note(
                    "未完成的缓存运行目录已移到："
                    f"{archive}"
                )
            except BaseException as archive_error:
                error.add_note(
                    "缓存运行清单写入失败，且未完成目录归档失败："
                    f"{archive_error}"
                )
            raise
        return run

    def cleanup_run(self, run: str | Path) -> None:
        path = Path(run).resolve()
        self._require_managed(path)
        if path.parent.name != "runs":
            raise ValueError(f"Only cache run directories can be removed: {path}")
        manifest = self._run_manifest(path)
        if manifest is None:
            raise ValueError(
                "Cache run is not owned by the current run-manifest schema: "
                f"{path}"
            )
        if (
            manifest.owner_pid != os.getpid()
            and process_is_alive(manifest.owner_pid)
        ):
            raise RuntimeError(
                "Cache run is still owned by process "
                f"{manifest.owner_pid}: {path}"
            )
        shutil.rmtree(path)

    def prune_runs(self, *, max_age_seconds: int = 24 * 60 * 60) -> None:
        if not self.root.is_dir():
            return
        cutoff = time.time() - max(0, max_age_seconds)
        for runs in self.root.rglob("runs"):
            if not runs.is_dir():
                continue
            for candidate in runs.iterdir():
                if not candidate.is_dir():
                    continue
                try:
                    modified = candidate.stat().st_mtime
                except OSError:
                    continue
                if modified < cutoff and not self._run_is_live(candidate):
                    try:
                        self.cleanup_run(candidate)
                    except (OSError, RuntimeError, ValueError):
                        continue

    def prune_files(
        self,
        relative_directory: str | Path,
        pattern: str,
        *,
        keep: int,
        max_age_seconds: int,
    ) -> None:
        directory = (self.root / relative_directory).resolve()
        self._require_managed(directory)
        if not directory.is_dir():
            return
        files_with_mtime: list[tuple[Path, float]] = []
        for path in directory.glob(pattern):
            try:
                source_stat = path.stat()
            except OSError:
                continue
            if stat.S_ISREG(source_stat.st_mode):
                files_with_mtime.append((path, source_stat.st_mtime))
        files = [
            path
            for path, _modified in sorted(
                files_with_mtime,
                key=lambda item: item[1],
                reverse=True,
            )
        ]
        cutoff = time.time() - max(0, max_age_seconds)
        for path in files[max(0, keep) :]:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _require_managed(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as error:
            raise ValueError(f"Cache path is outside the managed root: {path}") from error

    def _run_is_live(self, run: Path) -> bool:
        manifest = self._run_manifest(run)
        return manifest is None or process_is_alive(
            manifest.owner_pid
        )

    def _run_manifest(
        self,
        run: Path,
    ) -> _RunManifest | None:
        manifest = run / self.RUN_MANIFEST
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if set(payload) != {
            "schema_version",
            "owner_pid",
            "created_at_ns",
        }:
            return None
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"]
            != self.RUN_SCHEMA_VERSION
            or type(payload["owner_pid"]) is not int
            or type(payload["created_at_ns"]) is not int
            or payload["owner_pid"] <= 0
            or payload["created_at_ns"] <= 0
        ):
            return None
        try:
            run_id = str(uuid.UUID(run.name, version=4))
        except ValueError:
            return None
        if run.name != run_id:
            return None
        return _RunManifest(
            owner_pid=payload["owner_pid"],
            created_at_ns=payload["created_at_ns"],
        )
