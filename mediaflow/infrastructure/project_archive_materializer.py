from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from mediaflow.application.web_package_files import web_package_root
from mediaflow.domain.project import AssetFingerprint
from mediaflow.domain.web_manifest import WebAssetSpec
from mediaflow.file_digest import sha256_file

from .file_fingerprint import fingerprint_matches
from .task_repository import TaskRepository


class ProjectArchiveMaterializer:
    """Close every named version's file dependencies inside an archive copy."""

    def __init__(
        self, source_root: Path, staging: Path, *, check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        self.source_root = source_root.resolve()
        self.staging = staging.resolve()
        self._collected: dict[str, list[Path]] = {}
        self._check_cancelled = check_cancelled or (lambda: None)

    def materialize(self) -> None:
        self._database(self.staging / "project.mfp", main=True)

    def _database(self, database_path: Path, *, main: bool = False) -> str:
        self._check_cancelled()
        with closing(sqlite3.connect(database_path)) as connection:
            connection.row_factory = sqlite3.Row
            if main:
                for row in connection.execute("SELECT collection_json FROM project_collection"):
                    for item in json.loads(row[0])["items"]:
                        self._collected.setdefault(item["asset_id"], []).append(
                            self.source_root / item["collected_path"]
                        )
            with connection:
                self._assets(connection)
                self._creative_files(connection)
                self._collections(connection)
                self._task_history(connection)
                # Restore preserves the live catalog. Embedded historical
                # catalogs are not restorable content and their hashes become
                # obsolete after migrations. Only the main catalog owns versions.
                versions = (
                    connection.execute("SELECT id, snapshot_path, sha256 FROM project_version").fetchall()
                    if main
                    else []
                )
                if not main:
                    connection.execute("DELETE FROM project_version")
                for version in versions:
                    relative = Path(version["snapshot_path"])
                    original = (self.source_root / relative).resolve()
                    target = (self.staging / relative).resolve()
                    if not original.is_relative_to(self.source_root / "generated" / "versions"):
                        raise ValueError("命名版本路径不在版本目录中")
                    if not original.is_file() or sha256_file(original) != version["sha256"]:
                        raise RuntimeError(f"命名版本快照校验失败：{version['id']}")
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self._copy_bytes(original, target)
                    digest = self._database(target)
                    connection.execute(
                        "UPDATE project_version SET sha256=? WHERE id=?", (digest, version["id"])
                    )
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise RuntimeError("归档副本的项目关系校验失败")
        digest = sha256_file(database_path)
        return digest

    def _assets(self, connection: sqlite3.Connection) -> None:
        for asset in connection.execute("SELECT * FROM asset").fetchall():
            path = Path(asset["path"])
            source = path.resolve() if path.is_absolute() else (self.source_root / path).resolve()
            fingerprint = (
                AssetFingerprint.model_validate_json(asset["fingerprint_json"])
                if asset["fingerprint_json"]
                else None
            )
            candidates = [source, *self._collected.get(asset["id"], [])]
            selected_source = next(
                (
                    item
                    for item in candidates
                    if item.is_file() and (fingerprint is None or fingerprint_matches(item, fingerprint))
                ),
                None,
            )
            if selected_source is None:
                raise FileNotFoundError(f"命名版本素材缺失或已变化，无法完整归档：{asset['name']}")
            source = selected_source
            if asset["kind"] == "web":
                # An editable webpage is a package, not only its HTML entry.
                spec_row = connection.execute(
                    "SELECT manifest_json, source_hash FROM web_asset WHERE asset_id=?", (asset["id"],)
                ).fetchone()
                if spec_row is None:
                    raise RuntimeError("网页素材缺少包合同")
                spec = WebAssetSpec(
                    asset_id=asset["id"],
                    manifest=json.loads(spec_row["manifest_json"]),
                    source_hash=spec_row["source_hash"],
                )
                package = web_package_root(source, spec.manifest)
                if package.parent != self.source_root / "sources" / "web":
                    raise ValueError("归档网页素材必须来自已发布的受管包")
                self._copy(package, package.relative_to(self.source_root))
                token = package.name.removeprefix("p-")
                receipt = self.source_root / "sources" / "web" / "receipts" / f"r-{token}.json"
                self._copy(receipt, receipt.relative_to(self.source_root))
                relative = source.relative_to(self.source_root)
            else:
                relative = self._copy_file(source)
            connection.execute(
                "UPDATE asset SET path=?, managed=1, proxy_path=NULL, "
                "sdr_preview_proxy_path=NULL, waveform_path=NULL WHERE id=?",
                (relative.as_posix(), asset["id"]),
            )

    def _creative_files(self, connection: sqlite3.Connection) -> None:
        # These are authored or synthesized inputs not necessarily registered
        # as timeline assets yet. Their existing domain contracts are relative.
        for table, column, digest_column in (
            ("dubbing_reference", "path", "sha256"),
            ("dubbing_utterance", "output_path", "output_sha256"),
            ("dubbing_session", "master_path", "master_sha256"),
        ):
            for row in connection.execute(
                f"SELECT {column}, {digest_column} FROM {table} WHERE {column} IS NOT NULL"
            ):
                relative = Path(row[0])
                source = (self.source_root / relative).resolve()
                if relative.is_absolute() or not source.is_relative_to(self.source_root):
                    raise ValueError("配音素材必须位于项目目录内")
                if not source.is_file() or (row[1] and sha256_file(source) != row[1]):
                    raise RuntimeError(f"配音素材缺失或校验失败：{relative}")
                self._copy(source, relative)
        for row in connection.execute("SELECT thread_json FROM review_thread"):
            for snapshot in json.loads(row[0])["snapshots"]:
                relative = Path(snapshot["relative_path"])
                source = (self.source_root / relative).resolve()
                if not source.is_relative_to(self.source_root):
                    raise ValueError("审阅截图必须位于项目目录内")
                if not source.is_file() or sha256_file(source) != snapshot["sha256"]:
                    raise RuntimeError(f"审阅截图缺失或校验失败：{relative}")
                self._copy(source, relative)

    def _collections(self, connection: sqlite3.Connection) -> None:
        for row in connection.execute("SELECT id, collection_json FROM project_collection").fetchall():
            record = json.loads(row["collection_json"])
            for item in record["items"]:
                asset = connection.execute(
                    "SELECT path FROM asset WHERE id=?", (item["asset_id"],)
                ).fetchone()
                if asset is None:
                    raise RuntimeError("归档素材归集记录缺少对应素材")
                # Both undo directions must remain inside the portable copy.
                item["original_path"] = asset["path"]
                item["original_managed"] = True
                item["collected_path"] = asset["path"]
            connection.execute(
                "UPDATE project_collection SET collection_json=? WHERE id=?",
                (json.dumps(record, ensure_ascii=False), row["id"]),
            )

    @staticmethod
    def _task_history(connection: sqlite3.Connection) -> None:
        connection.execute("UPDATE task SET execution_scope='archive_history'")
        connection.execute(
            "UPDATE task SET status='cancelled', execution_owner_id=NULL, heartbeat_at=NULL, "
            "lease_expires_at=NULL, stop_request=NULL, revision=revision+1, "
            "error='归档副本中的任务仅作为历史保留，请指定当前输出位置并创建新任务' "
            "WHERE status IN ('pending', 'running', 'paused')"
        )
        for row in connection.execute("SELECT * FROM task").fetchall():
            TaskRepository._insert_event(connection, TaskRepository._from_row(row), "archive_history")
        connection.execute(
            "UPDATE workflow_run SET status='cancelled' "
            "WHERE status NOT IN ('completed','failed','cancelled')"
        )
        connection.execute("UPDATE undo_group SET state='discarded'")

    def _copy_file(self, source: Path) -> Path:
        if source.is_relative_to(self.source_root):
            relative = source.relative_to(self.source_root)
        else:
            relative = Path("sources") / "archive-media" / sha256_file(source)[:24] / source.name
        self._copy(source, relative)
        return relative

    def _copy(self, source: Path, relative: Path) -> None:
        destination = (self.staging / relative).resolve()
        if not destination.is_relative_to(self.staging):
            raise ValueError("归档素材路径超出目标项目")
        if not source.exists():
            raise FileNotFoundError(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if not destination.exists():
                shutil.copytree(source, destination, copy_function=self._copy_bytes)
            for item in source.rglob("*"):
                copied = destination / item.relative_to(source)
                if item.is_file() and sha256_file(item) != sha256_file(copied):
                    raise RuntimeError(f"归档素材复制校验失败：{item}")
        else:
            if not destination.exists():
                self._copy_bytes(source, destination)
            if sha256_file(source) != sha256_file(destination):
                raise RuntimeError(f"归档素材复制校验失败：{source}")

    def _copy_bytes(self, source: str | Path, destination: str | Path) -> str:
        with Path(source).open("rb") as reader, Path(destination).open("wb") as writer:
            while chunk := reader.read(1024 * 1024):
                self._check_cancelled()
                writer.write(chunk)
        shutil.copystat(source, destination)
        return str(destination)
