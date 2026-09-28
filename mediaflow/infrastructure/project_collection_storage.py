from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from mediaflow.application.project_collection_storage import (
    CollectedAssetCopy,
    ProjectCollectionStorage,
)
from mediaflow.domain.model_base import now_ms


class LocalProjectCollectionStorage(ProjectCollectionStorage):
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir.resolve()
        self.collection_root = self.project_dir / "sources" / "collected"

    def content_size(self, source: Path) -> int:
        source = source.resolve(strict=True)
        if source.is_file():
            return source.stat().st_size
        if not source.is_dir():
            raise ValueError(f"素材不是文件或目录：{source}")
        return sum(item.stat().st_size for item in self._files(source))

    def collect(
        self,
        collection_id: str,
        sources: list[tuple[str, Path]],
    ) -> list[CollectedAssetCopy]:
        destination = self.collection_root / collection_id
        staging = self.collection_root / f".{collection_id}.partial"
        if destination.exists() or staging.exists():
            raise FileExistsError(f"项目归集目录已存在：{destination}")
        staging.mkdir(parents=True, exist_ok=False)
        copied: list[CollectedAssetCopy] = []
        try:
            for asset_id, source_value in sources:
                source = source_value.resolve(strict=True)
                self._validate_source(source)
                asset_dir = staging / asset_id
                asset_dir.mkdir(parents=True, exist_ok=False)
                target = asset_dir / source.name
                source_size, source_hash = self._content_identity(source)
                if source.is_dir():
                    shutil.copytree(source, target, copy_function=shutil.copy2)
                else:
                    shutil.copy2(source, target)
                copied_size, copied_hash = self._content_identity(target)
                if (copied_size, copied_hash) != (source_size, source_hash):
                    raise RuntimeError(f"归集后的素材校验失败：{source.name}")
                copied.append(
                    CollectedAssetCopy(
                        asset_id=asset_id,
                        path=destination / asset_id / source.name,
                        size_bytes=copied_size,
                        sha256=copied_hash,
                    )
                )
            staging.replace(destination)
            return copied
        except BaseException:
            self._archive_path(staging, collection_id)
            raise

    def verify(self, path: Path, expected_sha256: str) -> bool:
        try:
            _size, digest = self._content_identity(path.resolve(strict=True))
        except (OSError, ValueError):
            return False
        return digest == expected_sha256

    def archive_uncommitted(self, collection_id: str) -> Path | None:
        return self._archive_path(self.collection_root / collection_id, collection_id)

    def _archive_path(self, source: Path, collection_id: str) -> Path | None:
        if not source.exists():
            return None
        archive = (
            self.project_dir
            / "archive"
            / "failed-collections"
            / f"{collection_id}-{now_ms()}"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        source.replace(archive)
        return archive

    def _content_identity(self, source: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        if source.is_file():
            size = self._hash_file(source, digest)
            return size, digest.hexdigest()
        if not source.is_dir():
            raise ValueError(f"素材不是文件或目录：{source}")
        size = 0
        for file_path in self._files(source):
            relative = file_path.relative_to(source).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            size += self._hash_file(file_path, digest)
        return size, digest.hexdigest()

    @staticmethod
    def _hash_file(path: Path, digest) -> int:
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return size

    @staticmethod
    def _files(root: Path) -> list[Path]:
        files = []
        for item in root.rglob("*"):
            if item.is_symlink():
                raise ValueError(f"项目归集不接受符号链接：{item}")
            if item.is_file():
                files.append(item)
        return sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold())

    @staticmethod
    def _validate_source(source: Path) -> None:
        if source.is_symlink():
            raise ValueError(f"项目归集不接受符号链接：{source}")
        if not source.is_file() and not source.is_dir():
            raise ValueError(f"素材不是文件或目录：{source}")
