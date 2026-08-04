from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.file_digest import sha256_file


def migrate_version_snapshots(
    workspace,
    connection: sqlite3.Connection,
    *,
    source_version: int | tuple[int, ...],
    target_version: int,
    migrate_database: Callable[[sqlite3.Connection], None],
) -> None:
    """Publish snapshot migrations atomically with their catalog hash updates."""

    source_versions = (
        (source_version,)
        if isinstance(source_version, int)
        else source_version
    )
    versions_root = (workspace.project_dir / "generated" / "versions").resolve()
    archive_root = versions_root / "archive"
    rows = connection.execute(
        "SELECT id, snapshot_path, sha256 FROM project_version ORDER BY created_at, id"
    ).fetchall()
    for row in rows:
        record_id = str(row["id"])
        snapshot_path = (workspace.project_dir / str(row["snapshot_path"])).resolve()
        if not snapshot_path.is_relative_to(versions_root):
            raise ValueError("Project version snapshot escaped the managed versions directory")
        if not snapshot_path.is_file():
            raise FileNotFoundError(snapshot_path)
        old_sha = sha256_file(snapshot_path)
        if old_sha != str(row["sha256"]):
            raise RuntimeError(f"Project version snapshot checksum changed: {record_id}")
        with closing(sqlite3.connect(snapshot_path)) as probe:
            version_row = probe.execute(
                "SELECT version FROM schema_info WHERE component='project'"
            ).fetchone()
        if version_row is None:
            raise RuntimeError(f"Project version snapshot has no schema version: {record_id}")
        version = int(version_row[0])
        if version == target_version:
            continue
        if version not in source_versions:
            raise RuntimeError(
                f"Project version snapshot schema {version} cannot migrate "
                f"from schemas {source_versions}"
            )

        archive_root.mkdir(parents=True, exist_ok=True)
        staged = unique_temporary_sibling(
            snapshot_path,
            label=f"migrate-v{target_version}",
        )
        archived_original = (
            archive_root / f"pre-v{target_version}-{record_id}-{old_sha[:12]}.mfp"
        )
        if archived_original.exists():
            raise FileExistsError(archived_original)
        shutil.copy2(snapshot_path, staged)
        try:
            with closing(sqlite3.connect(staged)) as staged_connection:
                staged_connection.row_factory = sqlite3.Row
                with staged_connection:
                    migrate_database(staged_connection)
                    staged_connection.execute(
                        "UPDATE schema_info SET version=? WHERE component='project'",
                        (target_version,),
                    )
                integrity = staged_connection.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or str(integrity[0]).casefold() != "ok":
                    raise RuntimeError(
                        f"Migrated project version snapshot failed integrity check: {record_id}"
                    )
                if staged_connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise RuntimeError(
                        f"Migrated project version snapshot has invalid relations: {record_id}"
                    )
        except BaseException:
            failed = unique_temporary_sibling(
                archive_root / f"failed-v{target_version}-{record_id}.mfp",
                label="snapshot",
            )
            staged.replace(failed)
            raise

        snapshot_path.replace(archived_original)
        try:
            staged.replace(snapshot_path)
        except BaseException:
            archived_original.replace(snapshot_path)
            raise

        def rollback_snapshot(
            _error: BaseException,
            *,
            current: Path = snapshot_path,
            original: Path = archived_original,
            identity: str = record_id,
        ) -> None:
            if current.exists():
                failed = unique_temporary_sibling(
                    archive_root / f"rollback-v{target_version}-{identity}.mfp",
                    label="snapshot",
                )
                current.replace(failed)
            original.replace(current)

        workspace.enlist_transaction_publication(
            on_commit=lambda: None,
            on_rollback=rollback_snapshot,
        )
        connection.execute(
            "UPDATE project_version SET sha256=? WHERE id=?",
            (sha256_file(snapshot_path), record_id),
        )
