from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from mediaflow.domain.project_records import ExportHistoryRecord, ProjectVersionRecord


class ProjectRecordsRepository:
    def save_export_history(self, record: ExportHistoryRecord) -> ExportHistoryRecord:
        self.get_sequence(record.sequence_id)
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
        project = self.get_project()
        record = ProjectVersionRecord(
            name=normalized,
            snapshot_path="generated/versions/pending.mfp",
            content_revision=self.content_revision(),
        )
        relative_path = f"generated/versions/{record.id}.mfp"
        record = record.model_copy(update={"snapshot_path": relative_path})
        snapshot_path = self.project_dir / Path(relative_path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            self._insert_project_version(connection, project.id, record)
        with self._connection_lock:
            with sqlite3.connect(snapshot_path) as snapshot:
                self._connection.backup(snapshot)
        digest = self._file_sha256(snapshot_path)
        record = record.model_copy(update={"sha256": digest})
        with self.transaction() as connection:
            self._insert_project_version(connection, project.id, record)
        return record

    def list_project_versions(self) -> list[ProjectVersionRecord]:
        project = self.get_project()
        rows = self._fetchall(
            """SELECT id, name, snapshot_path, sha256, content_revision, created_at
               FROM project_version WHERE project_id=? ORDER BY created_at DESC, id""",
            (project.id,),
        )
        return [ProjectVersionRecord(**dict(row)) for row in rows]

    def restore_project_version(self, version_id: str) -> ProjectVersionRecord:
        versions = self.list_project_versions()
        try:
            record = next(item for item in versions if item.id == version_id)
        except StopIteration as error:
            raise KeyError(version_id) from error
        snapshot_path = self._version_snapshot_path(record.snapshot_path)
        if not snapshot_path.is_file():
            raise FileNotFoundError(snapshot_path)
        if self._file_sha256(snapshot_path) != record.sha256:
            raise RuntimeError("命名版本快照校验失败")
        with sqlite3.connect(snapshot_path) as source:
            version_row = source.execute(
                "SELECT version FROM schema_info WHERE component='project'"
            ).fetchone()
            if version_row is None or int(version_row[0]) != self._project_schema_version():
                raise RuntimeError("命名版本快照的项目格式不受支持")
            with self._connection_lock:
                source.backup(self._connection)
                self._connection.commit()
        self.acknowledge_content_revision()
        project = self.get_project()
        with self.transaction() as connection:
            for preserved in versions:
                self._insert_project_version(connection, project.id, preserved)
        self.acknowledge_content_revision()
        return record

    def _version_snapshot_path(self, relative_path: str) -> Path:
        versions_root = (self.project_dir / "generated" / "versions").resolve()
        path = (self.project_dir / relative_path).resolve()
        if not path.is_relative_to(versions_root):
            raise ValueError("命名版本快照必须位于项目版本目录")
        return path

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

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

    @staticmethod
    def _project_schema_version() -> int:
        from mediaflow.infrastructure.project_schema import PROJECT_SCHEMA_VERSION

        return PROJECT_SCHEMA_VERSION
