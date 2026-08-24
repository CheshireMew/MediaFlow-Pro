from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.model_base import new_id, now_ms
from mediaflow.domain.project_records import ExportHistoryRecord, ProjectVersionRecord
from mediaflow.file_digest import sha256_file

from .project_repository_component import ProjectRepositoryComponent
from .project_schema_definition import PROJECT_SCHEMA_VERSION
from .sqlite_connections import open_project_database

if TYPE_CHECKING:
    from .project_database_session import ProjectDatabaseSession
    from .project_metadata_repository import ProjectMetadataRepository
    from .sequence_catalog_repository import SequenceCatalogRepository

_TERMINAL_TASK_STATUSES = tuple(
    status.value for status in TaskStatus if status.is_terminal
)


class ProjectRecordsRepository(ProjectRepositoryComponent):
    def __init__(
        self,
        database: ProjectDatabaseSession,
        *,
        projects: Callable[[], ProjectMetadataRepository],
        sequences: Callable[[], SequenceCatalogRepository],
    ) -> None:
        super().__init__(database)
        self._projects = projects
        self._sequences = sequences

    def save_export_history(self, record: ExportHistoryRecord) -> ExportHistoryRecord:
        self._sequences().get_sequence(record.sequence_id)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO export_history(
                       id, task_id, sequence_id, output_path, format, preset_json,
                       quality_json, content_revision, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       task_id=excluded.task_id,
                       sequence_id=excluded.sequence_id,
                       output_path=excluded.output_path,
                       format=excluded.format,
                       preset_json=excluded.preset_json,
                       quality_json=excluded.quality_json,
                       content_revision=excluded.content_revision,
                       created_at=excluded.created_at""",
                (
                    record.id,
                    record.task_id,
                    record.sequence_id,
                    record.output_path,
                    record.format.value,
                    json.dumps(record.preset, ensure_ascii=False, separators=(",", ":")),
                    record.quality.model_dump_json(),
                    record.content_revision,
                    record.created_at,
                ),
            )
        return record

    def list_export_history(
        self,
        sequence_id: str | None = None,
    ) -> list[ExportHistoryRecord]:
        rows = self._fetchall(
            (
                "SELECT * FROM export_history WHERE sequence_id=? ORDER BY created_at DESC, id"
                if sequence_id
                else "SELECT * FROM export_history ORDER BY created_at DESC, id"
            ),
            (sequence_id,) if sequence_id else (),
        )
        return [self._export_history_from_row(row) for row in rows]

    def get_export_history(self, record_id: str) -> ExportHistoryRecord:
        row = self._fetchone("SELECT * FROM export_history WHERE id=?", (record_id,))
        if row is None:
            raise KeyError(record_id)
        return self._export_history_from_row(row)

    def create_project_version(self, name: str) -> ProjectVersionRecord:
        normalized = " ".join(name.split())
        if not normalized:
            raise ValueError("版本名称不能为空")
        record_id = new_id()
        relative_path = f"generated/versions/{record_id}.mfp"
        snapshot_path = self.project_dir / Path(relative_path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = unique_temporary_sibling(snapshot_path, label="version")
        published = False
        try:
            with self.transaction() as connection:
                self._require_no_active_tasks(connection)
                project_row = connection.execute(
                    "SELECT id, content_revision FROM project LIMIT 1"
                ).fetchone()
                if project_row is None:
                    raise RuntimeError("Project record is missing")
                content_revision = int(project_row["content_revision"])

                # The repository transaction holds BEGIN IMMEDIATE, so task
                # writers cannot change the database while this separate
                # read-only connection captures the last committed state.
                with closing(
                    open_project_database(self._database.database_path, read_only=True)
                ) as source:
                    with closing(sqlite3.connect(temporary)) as snapshot, snapshot:
                        source.backup(snapshot)
                self._validate_snapshot_database(
                    temporary,
                    expected_project_id=str(project_row["id"]),
                    expected_content_revision=content_revision,
                )
                record = ProjectVersionRecord(
                    id=record_id,
                    name=normalized,
                    snapshot_path=relative_path,
                    sha256=sha256_file(temporary),
                    content_revision=content_revision,
                )
                self._insert_project_version(
                    connection,
                    str(project_row["id"]),
                    record,
                )
                self._touch_project(connection)
                temporary.replace(snapshot_path)
                published = True

                def rollback_snapshot(_error: BaseException) -> None:
                    self._archive_failed_snapshot(
                        snapshot_path,
                        record_id,
                    )

                self._enlist_transaction_publication(
                    on_commit=lambda: None,
                    on_rollback=rollback_snapshot,
                )
            return record
        except BaseException:
            failed = snapshot_path if published else temporary
            self._archive_failed_snapshot(failed, record_id)
            raise

    def list_project_versions(self) -> list[ProjectVersionRecord]:
        project = self._projects().get_project()
        rows = self._fetchall(
            """SELECT id, name, snapshot_path, sha256, content_revision, created_at
               FROM project_version WHERE project_id=? ORDER BY created_at DESC, id""",
            (project.id,),
        )
        return [ProjectVersionRecord(**dict(row)) for row in rows]

    def restore_project_version(self, version_id: str) -> ProjectVersionRecord:
        with self._connection_lock:
            versions = self.list_project_versions()
            try:
                record = next(item for item in versions if item.id == version_id)
            except StopIteration as error:
                raise KeyError(version_id) from error
            snapshot_path = self._version_snapshot_path(record.snapshot_path)
            if not snapshot_path.is_file():
                raise FileNotFoundError(snapshot_path)
            if sha256_file(snapshot_path) != record.sha256:
                raise RuntimeError("命名版本快照校验失败")

            project = self._projects().get_project()
            self._validate_snapshot_database(
                snapshot_path,
                expected_project_id=project.id,
            )
            with closing(open_project_database(snapshot_path, read_only=True)) as snapshot:
                with self.transaction() as connection:
                    self._require_no_active_tasks(connection)
                    current_revision = self.content_revision()
                    self._require_matching_schema(connection, snapshot)
                    snapshot_revision = self._snapshot_project_revision(snapshot)
                    self._replace_project_tables(
                        connection,
                        snapshot=snapshot,
                        preserved_versions=versions,
                        project_id=project.id,
                    )
                    restored_revision = max(
                        current_revision,
                        snapshot_revision,
                    ) + 1
                    cursor = connection.execute(
                        """UPDATE project
                           SET content_revision=?, updated_at=?
                           WHERE id=?""",
                        (restored_revision, now_ms(), project.id),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("命名版本快照不属于当前项目")
                    foreign_key_errors = connection.execute(
                        "PRAGMA main.foreign_key_check"
                    ).fetchall()
                    if foreign_key_errors:
                        raise RuntimeError("命名版本快照包含无效项目关系")
        return record

    def _validate_snapshot_database(
        self,
        path: Path,
        *,
        expected_project_id: str,
        expected_content_revision: int | None = None,
    ) -> None:
        with closing(open_project_database(path, read_only=True)) as source:
            version_row = source.execute(
                "SELECT version FROM schema_info WHERE component='project'"
            ).fetchone()
            if version_row is None or int(version_row[0]) != PROJECT_SCHEMA_VERSION:
                raise RuntimeError("命名版本快照的项目格式不受支持")
            integrity = source.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise RuntimeError("命名版本快照完整性检查失败")
            if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("命名版本快照包含无效项目关系")
            project_row = source.execute(
                "SELECT id, content_revision FROM project LIMIT 1"
            ).fetchone()
            if project_row is None or str(project_row["id"]) != expected_project_id:
                raise RuntimeError("命名版本快照不属于当前项目")
            if (
                expected_content_revision is not None
                and int(project_row["content_revision"]) != expected_content_revision
            ):
                raise RuntimeError("命名版本快照未捕获一致的项目状态")
            self._require_no_active_tasks(source, snapshot=True)

    @staticmethod
    def _application_tables(
        connection: sqlite3.Connection,
        schema: str,
    ) -> list[str]:
        rows = connection.execute(
            f"""SELECT name
                FROM {schema}.sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name"""
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _require_matching_schema(
        self,
        connection: sqlite3.Connection,
        snapshot: sqlite3.Connection,
    ) -> None:
        main_tables = self._application_tables(connection, "main")
        snapshot_tables = self._application_tables(snapshot, "main")
        if snapshot_tables != main_tables:
            raise RuntimeError("命名版本快照的项目结构不完整")
        for table in main_tables:
            identifier = self._quote_identifier(table)
            main_columns = connection.execute(
                f"PRAGMA main.table_info({identifier})"
            ).fetchall()
            snapshot_columns = snapshot.execute(
                f"PRAGMA main.table_info({identifier})"
            ).fetchall()
            if [tuple(row) for row in snapshot_columns] != [
                tuple(row) for row in main_columns
            ]:
                raise RuntimeError("命名版本快照的项目结构不受支持")

    def _snapshot_project_revision(
        self,
        snapshot: sqlite3.Connection,
    ) -> int:
        row = snapshot.execute(
            "SELECT id, content_revision FROM project LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("命名版本快照缺少项目记录")
        self._require_no_active_tasks(
            snapshot,
            snapshot=True,
        )
        return int(row["content_revision"])

    def _replace_project_tables(
        self,
        connection: sqlite3.Connection,
        *,
        snapshot: sqlite3.Connection,
        preserved_versions: list[ProjectVersionRecord],
        project_id: str,
    ) -> None:
        tables = [
            table
            for table in self._application_tables(connection, "main")
            if table != "schema_info"
        ]
        insertion_order = self._foreign_key_insertion_order(connection, tables)
        connection.execute("PRAGMA defer_foreign_keys=ON")
        for table in tables:
            identifier = self._quote_identifier(table)
            for foreign_key in connection.execute(
                f"PRAGMA main.foreign_key_list({identifier})"
            ).fetchall():
                if str(foreign_key["table"]) != table:
                    continue
                column = self._quote_identifier(str(foreign_key["from"]))
                connection.execute(
                    f"UPDATE {identifier} SET {column}=NULL"
                )
        for table in reversed(insertion_order):
            connection.execute(f"DELETE FROM {self._quote_identifier(table)}")
        for table in insertion_order:
            if table == "project_version":
                continue
            identifier = self._quote_identifier(table)
            rows = snapshot.execute(
                f"SELECT * FROM main.{identifier}"
            ).fetchall()
            if not rows:
                continue
            placeholders = ",".join("?" for _ in range(len(rows[0])))
            connection.executemany(
                f"INSERT INTO main.{identifier} VALUES ({placeholders})",
                (tuple(row) for row in rows),
            )
        for preserved in preserved_versions:
            self._insert_project_version(
                connection,
                project_id,
                preserved,
            )

    def _foreign_key_insertion_order(
        self,
        connection: sqlite3.Connection,
        tables: list[str],
    ) -> list[str]:
        table_set = set(tables)
        dependencies: dict[str, set[str]] = {}
        for table in tables:
            identifier = self._quote_identifier(table)
            dependencies[table] = {
                str(row["table"])
                for row in connection.execute(
                    f"PRAGMA main.foreign_key_list({identifier})"
                ).fetchall()
                if str(row["table"]) in table_set
                and str(row["table"]) != table
            }
        ordered: list[str] = []
        remaining = set(tables)
        while remaining:
            ready = sorted(
                table
                for table in remaining
                if dependencies[table].isdisjoint(remaining)
            )
            if not ready:
                raise RuntimeError("当前项目结构包含无法恢复的循环关系")
            ordered.extend(ready)
            remaining.difference_update(ready)
        return ordered

    @staticmethod
    def _require_no_active_tasks(
        connection: sqlite3.Connection,
        *,
        schema: str = "main",
        snapshot: bool = False,
    ) -> None:
        placeholders = ",".join("?" for _ in _TERMINAL_TASK_STATUSES)
        row = connection.execute(
            f"""SELECT id FROM {schema}.task
                WHERE status NOT IN ({placeholders})
                LIMIT 1""",
            _TERMINAL_TASK_STATUSES,
        ).fetchone()
        if row is not None:
            target = "命名版本快照" if snapshot else "当前项目"
            raise RuntimeError(f"{target}仍有未完成任务，请等待任务结束后重试")

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def _archive_failed_snapshot(self, path: Path, record_id: str) -> Path | None:
        if not path.exists():
            return None
        archive = self.project_dir / "generated" / "versions" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        failed_at = now_ms()
        identity = hashlib.sha256(
            f"{record_id}\0{path.name}\0{failed_at}".encode()
        ).hexdigest()[:24]
        destination = archive / (
            f"failed-{failed_at}-{identity}{path.suffix or '.mfp'}"
        )
        try:
            path.replace(destination)
        except OSError:
            return path if path.exists() else None
        return destination

    def _version_snapshot_path(self, relative_path: str) -> Path:
        versions_root = (self.project_dir / "generated" / "versions").resolve()
        path = (self.project_dir / relative_path).resolve()
        if not path.is_relative_to(versions_root):
            raise ValueError("命名版本快照必须位于项目版本目录")
        return path

    @staticmethod
    def _insert_project_version(
        connection: sqlite3.Connection,
        project_id: str,
        record: ProjectVersionRecord,
    ) -> None:
        connection.execute(
            """INSERT INTO project_version(
                   id, project_id, name, snapshot_path, sha256,
                   content_revision, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   project_id=excluded.project_id,
                   name=excluded.name,
                   snapshot_path=excluded.snapshot_path,
                   sha256=excluded.sha256,
                   content_revision=excluded.content_revision,
                   created_at=excluded.created_at""",
            (
                record.id,
                project_id,
                record.name,
                record.snapshot_path,
                record.sha256,
                record.content_revision,
                record.created_at,
            ),
        )

    @staticmethod
    def _export_history_from_row(row: sqlite3.Row) -> ExportHistoryRecord:
        return ExportHistoryRecord.model_validate(
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "sequence_id": row["sequence_id"],
                "output_path": row["output_path"],
                "format": row["format"],
                "preset": json.loads(row["preset_json"]),
                "quality": json.loads(row["quality_json"]),
                "content_revision": row["content_revision"],
                "created_at": row["created_at"],
            }
        )
