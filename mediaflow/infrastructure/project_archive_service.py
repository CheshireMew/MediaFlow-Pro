from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from mediaflow.domain.project_archive import (
    ProjectArchiveFile,
    ProjectArchiveManifest,
    ProjectArchiveResult,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.project_schema_definition import (
    PROJECT_FILE_NAME,
    PROJECT_SCHEMA_VERSION,
)


class ProjectArchiveService:
    """Create a verified, self-contained project directory without copying caches."""

    def __init__(self, repository: ProjectRepository) -> None:
        self.repository = repository

    def create(self, destination: str | Path) -> ProjectArchiveResult:
        target = Path(destination)
        if not target.is_absolute():
            raise ValueError("归档目标必须是绝对路径")
        target = target.resolve()
        staging = target.with_name(f".{target.name}.partial")
        if target.exists() or staging.exists():
            raise FileExistsError(f"归档目标已经存在：{target}")

        relative_sources = self._portable_sources()
        staging.mkdir(parents=True, exist_ok=False)
        try:
            self._backup_database(staging / PROJECT_FILE_NAME)
            for relative in relative_sources:
                self._copy_source(relative, staging)
            files = self._catalog_files(staging)
            manifest = ProjectArchiveManifest(
                project_id=self.repository.projects.get_project().id,
                project_name=self.repository.projects.get_project().name,
                project_schema_version=PROJECT_SCHEMA_VERSION,
                content_revision=self.repository.content_revision(),
                files=files,
            )
            manifest_path = staging / "archive-manifest.json"
            manifest_path.write_text(
                json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._verify_catalog(staging, files)
            self._validate_portable_project(staging, manifest)
            staging.replace(target)
        except BaseException as error:
            archived = self._archive_failure(staging, target)
            if archived is not None:
                error.add_note(f"未完成的归档已保留在：{archived}")
            raise
        return ProjectArchiveResult(
            destination=str(target),
            manifest_path=str(target / "archive-manifest.json"),
            file_count=len(files),
            total_bytes=sum(item.size_bytes for item in files),
            project_id=manifest.project_id,
            content_revision=manifest.content_revision,
        )

    def _portable_sources(self) -> list[Path]:
        root = self.repository.project_dir.resolve()
        sources: set[Path] = set()
        external: list[str] = []
        missing: list[str] = []
        for asset in self.repository.assets.list_assets():
            source = self.repository.assets.resolve_asset_path(asset).resolve()
            if not asset.managed or not source.is_relative_to(root):
                external.append(asset.name)
                continue
            if not source.exists():
                missing.append(asset.name)
                continue
            sources.add(source.relative_to(root))
        if external:
            raise ValueError(
                "归档前必须先归集所有外部素材：" + "、".join(sorted(external))
            )
        if missing:
            raise FileNotFoundError("以下项目素材已经丢失：" + "、".join(sorted(missing)))

        for version in self.repository.records.list_project_versions():
            snapshot = (root / version.snapshot_path).resolve()
            if not snapshot.is_relative_to(root) or not snapshot.is_file():
                raise FileNotFoundError(f"命名版本快照已经丢失：{version.name}")
            if self._sha256_file(snapshot) != version.sha256:
                raise RuntimeError(f"命名版本快照校验失败：{version.name}")
            sources.add(snapshot.relative_to(root))
        return self._collapse_sources(sources)

    @staticmethod
    def _collapse_sources(sources: set[Path]) -> list[Path]:
        ordered = sorted(sources, key=lambda item: (len(item.parts), item.as_posix().casefold()))
        selected: list[Path] = []
        for candidate in ordered:
            if any(candidate == root or candidate.is_relative_to(root) for root in selected):
                continue
            selected.append(candidate)
        return selected

    def _backup_database(self, destination: Path) -> None:
        # Automation exports run inside a project transaction.  Backing up that
        # same connection would wait on its own write transaction, so use an
        # independent read snapshot of the last committed project state.
        with (
            closing(sqlite3.connect(self.repository.database_path)) as source,
            closing(sqlite3.connect(destination)) as snapshot,
        ):
            source.execute("PRAGMA query_only=ON")
            source.backup(snapshot)
            integrity = snapshot.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise RuntimeError("归档项目数据库完整性检查失败")

    def _copy_source(self, relative: Path, staging: Path) -> None:
        source = self.repository.project_dir / relative
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, copy_function=shutil.copy2)
        else:
            shutil.copy2(source, destination)

    def _catalog_files(self, staging: Path) -> list[ProjectArchiveFile]:
        return [
            ProjectArchiveFile(
                path=path.relative_to(staging).as_posix(),
                size_bytes=path.stat().st_size,
                sha256=self._sha256_file(path),
            )
            for path in sorted(
                (item for item in staging.rglob("*") if item.is_file()),
                key=lambda item: item.relative_to(staging).as_posix().casefold(),
            )
        ]

    def _verify_catalog(self, staging: Path, files: list[ProjectArchiveFile]) -> None:
        for item in files:
            path = staging / item.path
            if path.stat().st_size != item.size_bytes or self._sha256_file(path) != item.sha256:
                raise RuntimeError(f"项目归档文件校验失败：{item.path}")

    @staticmethod
    def _validate_portable_project(
        staging: Path,
        manifest: ProjectArchiveManifest,
    ) -> None:
        with ProjectRepository.open(staging, writable=False) as archived:
            project = archived.projects.get_project()
            if project.id != manifest.project_id:
                raise RuntimeError("归档项目身份校验失败")
            if archived.content_revision() != manifest.content_revision:
                raise RuntimeError("归档项目修订校验失败")
            unavailable = [
                asset.name
                for asset in archived.assets.list_assets()
                if not asset.managed or not archived.assets.resolve_asset_path(asset).exists()
            ]
            if unavailable:
                raise RuntimeError("归档项目仍包含不可用素材：" + "、".join(unavailable))

    @staticmethod
    def _archive_failure(staging: Path, target: Path) -> Path | None:
        if not staging.exists():
            return None
        suffix = 1
        while True:
            candidate = target.with_name(f"{target.name}.failed-{suffix}")
            if not candidate.exists():
                staging.replace(candidate)
                return candidate
            suffix += 1

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
