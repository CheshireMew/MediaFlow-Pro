from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediaflow.domain.audio import AudioBus
from mediaflow.domain.enums import (
    SequenceKind,
)
from mediaflow.domain.model_base import new_id, now_ms
from mediaflow.domain.project import (
    Project,
    ProjectProfile,
    Sequence,
)
from mediaflow.domain.storage_names import require_project_root_path

from .audio_repository import AudioRepository
from .highlight_repository import HighlightRepository
from .project_catalog_repository import ProjectCatalogRepository
from .project_lock import ProjectWriteLock
from .project_migration_runner import ProjectSchemaMigrator
from .project_records_repository import ProjectRecordsRepository
from .project_schema_definition import (
    MANAGED_DIRECTORIES,
    PROJECT_FILE_NAME,
    PROJECT_SCHEMA_VERSION,
    SCHEMA_SQL,
)
from .project_serialization import json_value as _json
from .sqlite_uri import read_only_database_uri
from .subtitle_repository import SubtitleRepository
from .timeline_repository import TimelineRepository
from .web_media_repository import WebMediaRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _TransactionPublication:
    on_commit: Callable[[], None]
    on_rollback: Callable[[BaseException], None]


class ProjectRepository:
    def __init__(
        self,
        project_dir: Path,
        connection: sqlite3.Connection,
        *,
        read_only: bool,
        write_lock: ProjectWriteLock | None,
    ):
        self.project_dir = project_dir
        self.database_path = project_dir / PROJECT_FILE_NAME
        self._connection = connection
        self._connection_lock = threading.RLock()
        self._transaction_depth = 0
        self._savepoint_serial = 0
        self._transaction_publications: list[
            _TransactionPublication
        ] = []
        self.read_only = read_only
        self._write_lock = write_lock
        self._known_content_revision: int | None = None
        self.catalog = ProjectCatalogRepository(self)
        self.timeline = TimelineRepository(self)
        self.audio = AudioRepository(self)
        self.subtitles = SubtitleRepository(self)
        self.highlights = HighlightRepository(self)
        self.web = WebMediaRepository(self)
        self.records = ProjectRecordsRepository(self)

    @classmethod
    def create(
        cls,
        project_dir: str | Path,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> ProjectRepository:
        root = require_project_root_path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        database_path = root / PROJECT_FILE_NAME
        if database_path.exists():
            raise FileExistsError(f"Project already exists: {database_path}")
        creation_id = new_id().replace("-", "")[:12]
        temporary_path = root / f".creating-{creation_id}.mfp"
        for directory in MANAGED_DIRECTORIES:
            (root / directory).mkdir(exist_ok=True)

        lock = ProjectWriteLock(root / "cache" / "project.lock")
        if not lock.acquire():
            raise RuntimeError(f"Project directory is already locked: {root}")
        repository: ProjectRepository | None = None
        published = False
        try:
            connection = cls._connect(temporary_path, read_only=False)
            repository = cls(root, connection, read_only=False, write_lock=lock)
            repository._initialize(
                name=name,
                profile=profile or ProjectProfile(),
                profile_confirmed=profile is not None,
            )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                raise RuntimeError("New project database failed its integrity check")
            ProjectSchemaMigrator(repository).validate()
            project = repository.catalog.get_project()
            repository.catalog.get_sequence(project.main_sequence_id)
            repository.acknowledge_content_revision()
            with repository._connection_lock:
                repository._connection.close()
            repository._write_lock = None
            repository = None
            if database_path.exists():
                raise FileExistsError(f"Project already exists: {database_path}")
            temporary_path.rename(database_path)
            published = True
            final_connection = cls._connect(database_path, read_only=False)
            repository = cls(root, final_connection, read_only=False, write_lock=lock)
            repository.acknowledge_content_revision()
            return repository
        except BaseException as error:
            try:
                if repository is not None:
                    repository.close()
                else:
                    lock.release()
            except BaseException as cleanup_error:
                error.add_note(
                    f"创建项目失败后的资源释放失败：{cleanup_error}"
                )
            failed_path = (
                database_path if published else temporary_path
            )
            try:
                cls._archive_failed_creation(root, failed_path)
            except BaseException as archive_error:
                error.add_note(
                    f"创建失败数据库归档失败：{archive_error}"
                )
            raise

    @staticmethod
    def _archive_failed_creation(root: Path, database_path: Path) -> Path | None:
        if not database_path.exists():
            return None
        archive = root / "archive"
        archive.mkdir(exist_ok=True)
        creation_id = new_id().replace("-", "")[:12]
        destination = archive / f"create-failed-{now_ms()}-{creation_id}.mfp"
        database_path.rename(destination)
        return destination

    @classmethod
    def open(
        cls,
        project_dir: str | Path,
        *,
        writable: bool = True,
        cooperative: bool = False,
    ) -> ProjectRepository:
        root = require_project_root_path(project_dir).resolve(strict=True)
        database_path = root / PROJECT_FILE_NAME
        if not database_path.is_file():
            raise FileNotFoundError(database_path)
        if cooperative and not writable:
            raise ValueError("Cooperative project access is only meaningful for writable opens")

        lock: ProjectWriteLock | None = None
        read_only = not writable
        if writable and not cooperative:
            candidate = ProjectWriteLock(root / "cache" / "project.lock")
            if candidate.acquire():
                lock = candidate
            else:
                read_only = True
        connection: sqlite3.Connection | None = None
        repository: ProjectRepository | None = None
        try:
            connection = cls._connect(
                database_path,
                read_only=read_only,
            )
            repository = cls(
                root,
                connection,
                read_only=read_only,
                write_lock=lock,
            )
            ProjectSchemaMigrator(repository).validate()
            repository.acknowledge_content_revision()
            if not read_only:
                for directory in MANAGED_DIRECTORIES:
                    (root / directory).mkdir(exist_ok=True)
            return repository
        except BaseException as error:
            try:
                if repository is not None:
                    repository.close()
                else:
                    if connection is not None:
                        connection.close()
                    if lock is not None:
                        lock.release()
            except BaseException as cleanup_error:
                error.add_note(
                    f"打开项目失败后的资源释放失败：{cleanup_error}"
                )
            raise

    @property
    def owns_project_lock(self) -> bool:
        return self._write_lock is not None

    @property
    def known_content_revision(self) -> int:
        if self._known_content_revision is None:
            return self.acknowledge_content_revision()
        return self._known_content_revision

    def content_revision(self) -> int:
        row = self._fetchone("SELECT content_revision FROM project LIMIT 1")
        if row is None:
            raise RuntimeError("Project record is missing")
        return int(row["content_revision"])

    def acknowledge_content_revision(self) -> int:
        revision = self.content_revision()
        self._known_content_revision = revision
        return revision

    def automation_result(
        self,
        request_id: str,
        operation: str,
        input_hash: str,
    ) -> dict[str, Any] | None:
        row = self._fetchone(
            """SELECT operation, input_hash, result_json, state
               FROM automation_request WHERE request_id=?""",
            (request_id,),
        )
        if row is None:
            return None
        if row["operation"] != operation or row["input_hash"] != input_hash:
            raise ValueError("Automation request_id was reused with different input")
        if row["state"] == "running":
            return None
        if row["state"] != "completed":
            raise RuntimeError(
                f"Unknown automation request state: {row['state']}"
            )
        result = json.loads(str(row["result_json"]))
        if not isinstance(result, dict):
            raise RuntimeError("Persisted automation result is not a JSON object")
        return result

    def begin_automation_request(
        self,
        request_id: str,
        operation: str,
        input_hash: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO automation_request(
                       request_id, operation, input_hash, result_json,
                       state, created_at
                   ) VALUES (?, ?, ?, '{}', 'running', ?)
                   ON CONFLICT(request_id) DO NOTHING""",
                (request_id, operation, input_hash, now_ms()),
            )
            row = connection.execute(
                """SELECT operation, input_hash, result_json, state
                   FROM automation_request WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Automation request was not persisted")
            if row["operation"] != operation or row["input_hash"] != input_hash:
                raise ValueError(
                    "Automation request_id was reused with different input"
                )
            retrying = cursor.rowcount == 0
            if row["state"] == "running":
                return None, retrying
            if row["state"] != "completed":
                raise RuntimeError(
                    f"Unknown automation request state: {row['state']}"
                )
            stored = json.loads(str(row["result_json"]))
            if not isinstance(stored, dict):
                raise RuntimeError(
                    "Persisted automation result is not a JSON object"
                )
            return stored, retrying

    def save_automation_result(
        self,
        request_id: str,
        operation: str,
        input_hash: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        payload = _json(result)
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT operation, input_hash, result_json, state
                   FROM automation_request WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            if row is not None and (
                row["operation"] != operation
                or row["input_hash"] != input_hash
            ):
                raise ValueError("Automation request_id was reused with different input")
            if row is None:
                connection.execute(
                    """INSERT INTO automation_request(
                           request_id, operation, input_hash, result_json,
                           state, created_at
                       ) VALUES (?, ?, ?, ?, 'completed', ?)""",
                    (
                        request_id,
                        operation,
                        input_hash,
                        payload,
                        now_ms(),
                    ),
                )
            elif row["state"] == "running":
                connection.execute(
                    """UPDATE automation_request
                       SET result_json=?, state='completed'
                       WHERE request_id=? AND state='running'""",
                    (payload, request_id),
                )
            elif row["state"] != "completed":
                raise RuntimeError(
                    f"Unknown automation request state: {row['state']}"
                )
            row = connection.execute(
                """SELECT result_json, state FROM automation_request
                   WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            if row is None or row["state"] != "completed":
                raise RuntimeError("Automation result was not persisted")
            stored = json.loads(str(row["result_json"]))
            if not isinstance(stored, dict):
                raise RuntimeError("Persisted automation result is not a JSON object")
            return stored

    def consume_task_result_once(
        self,
        task_id: str,
        project_id: str,
        task_revision: int,
        action: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        with self.transaction() as connection:
            task_row = connection.execute(
                """SELECT project_id, status, revision
                   FROM task WHERE id=?""",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise KeyError(task_id)
            if (
                task_row["project_id"] != project_id
                or int(task_row["revision"]) != task_revision
            ):
                raise RuntimeError(
                    "Task changed before its result could be consumed"
                )
            if task_row["status"] not in {
                "completed",
                "failed",
                "cancelled",
            }:
                raise ValueError("Only terminal task results can be consumed")
            stored_row = connection.execute(
                """SELECT project_id, task_revision, result_json
                   FROM task_consumption WHERE task_id=?""",
                (task_id,),
            ).fetchone()
            if stored_row is not None:
                if (
                    stored_row["project_id"] != project_id
                    or int(stored_row["task_revision"]) != task_revision
                ):
                    raise RuntimeError(
                        "Persisted task consumption does not match the task"
                    )
                stored = json.loads(str(stored_row["result_json"]))
                if not isinstance(stored, dict):
                    raise RuntimeError(
                        "Persisted task consumption is not a JSON object"
                    )
                return stored, False

            result = action()
            if not isinstance(result, dict):
                raise TypeError("Task result consumer must return a dictionary")
            payload = _json(result)
            connection.execute(
                """INSERT INTO task_consumption(
                       task_id, project_id, task_revision,
                       result_json, created_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    task_id,
                    project_id,
                    task_revision,
                    payload,
                    now_ms(),
                ),
            )
            return json.loads(payload), True

    @staticmethod
    def _connect(database_path: Path, *, read_only: bool) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            if read_only:
                connection = sqlite3.connect(
                    read_only_database_uri(database_path),
                    uri=True,
                    timeout=5.0,
                    check_same_thread=False,
                )
            else:
                connection = sqlite3.connect(
                    database_path,
                    timeout=5.0,
                    check_same_thread=False,
                )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            if not read_only:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
            return connection
        except BaseException:
            if connection is not None:
                connection.close()
            raise

    def _initialize(
        self,
        *,
        name: str,
        profile: ProjectProfile,
        profile_confirmed: bool,
    ) -> None:
        project_id = new_id()
        main_sequence_id = new_id()
        created_at = now_ms()
        project = Project(
            id=project_id,
            name=name,
            main_sequence_id=main_sequence_id,
            created_at=created_at,
            updated_at=created_at,
        )
        sequence = Sequence(
            id=main_sequence_id,
            project_id=project_id,
            name="主序列",
            kind=SequenceKind.MAIN,
            profile=profile,
            profile_confirmed=profile_confirmed,
            created_at=created_at,
        )
        master_bus = AudioBus(sequence_id=main_sequence_id, name="主总线", position=0)
        dialogue_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="对白",
            parent_bus_id=master_bus.id,
            position=1,
        )
        music_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="音乐",
            parent_bus_id=master_bus.id,
            position=2,
        )
        effects_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="效果",
            parent_bus_id=master_bus.id,
            position=3,
        )
        with self.transaction() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "INSERT INTO schema_info(component, version) VALUES('project', ?)",
                (PROJECT_SCHEMA_VERSION,),
            )
            connection.execute(
                """INSERT INTO project(
                    id, name, main_sequence_id, workflow_auto_continue,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    project.id,
                    project.name,
                    project.main_sequence_id,
                    -1 if project.workflow_auto_continue is None else int(project.workflow_auto_continue),
                    project.created_at,
                    project.updated_at,
                ),
            )
            self.catalog._insert_sequence_record(connection, sequence)
            for bus in (master_bus, dialogue_bus, music_bus, effects_bus):
                self.audio._insert_bus_record(connection, bus)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise PermissionError("Project is open read-only")
        with self._connection_lock:
            if self._transaction_depth:
                publication_start = len(
                    self._transaction_publications
                )
                self._savepoint_serial += 1
                savepoint = (
                    f"mediaflow_nested_{self._transaction_depth}_"
                    f"{self._savepoint_serial}"
                )
                self._connection.execute(f"SAVEPOINT {savepoint}")
                self._transaction_depth += 1
                try:
                    yield self._connection
                    self._connection.execute(
                        f"RELEASE SAVEPOINT {savepoint}"
                    )
                except BaseException as error:
                    try:
                        self._connection.execute(
                            f"ROLLBACK TO SAVEPOINT {savepoint}"
                        )
                        self._connection.execute(
                            f"RELEASE SAVEPOINT {savepoint}"
                        )
                    except BaseException as rollback_error:
                        error.add_note(
                            "嵌套项目事务回滚失败："
                            f"{rollback_error}"
                        )
                    self._rollback_publications(
                        publication_start,
                        error,
                    )
                    raise
                finally:
                    self._transaction_depth -= 1
                return
            if self._transaction_publications:
                raise RuntimeError(
                    "Project transaction publication state leaked "
                    "from a previous transaction"
                )
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._transaction_depth = 1
                if self._known_content_revision is not None:
                    current_revision = self.content_revision()
                    if current_revision != self._known_content_revision:
                        raise RuntimeError(
                            "Project content changed in another process; reload before editing"
                        )
                yield self._connection
                self._connection.commit()
                self._known_content_revision = self._content_revision_if_available()
            except BaseException as error:
                try:
                    self._connection.rollback()
                except BaseException as rollback_error:
                    error.add_note(
                        f"项目数据库回滚失败：{rollback_error}"
                    )
                # Publication callbacks may need SQLite operations such as
                # DETACH DATABASE.  Run them only after the outer transaction
                # is observably over, not while the repository still reports
                # an active transaction.
                self._transaction_depth = 0
                self._rollback_publications(0, error)
                raise
            else:
                self._transaction_depth = 0
                self._commit_publications()
            finally:
                self._transaction_depth = 0
                self._transaction_publications.clear()

    def enlist_transaction_publication(
        self,
        *,
        on_commit: Callable[[], None],
        on_rollback: Callable[[BaseException], None],
    ) -> None:
        if self._transaction_depth <= 0:
            raise RuntimeError(
                "File publication must join an active project transaction"
            )
        self._transaction_publications.append(
            _TransactionPublication(
                on_commit=on_commit,
                on_rollback=on_rollback,
            )
        )

    def _rollback_publications(
        self,
        start: int,
        error: BaseException,
    ) -> None:
        publications = self._transaction_publications[start:]
        del self._transaction_publications[start:]
        for publication in reversed(publications):
            try:
                publication.on_rollback(error)
            except BaseException as rollback_error:
                error.add_note(
                    "项目文件发布回滚失败："
                    f"{rollback_error}"
                )

    def _commit_publications(self) -> None:
        publications = tuple(self._transaction_publications)
        self._transaction_publications.clear()
        for publication in publications:
            try:
                publication.on_commit()
            except BaseException:
                logger.exception(
                    "Project transaction committed, but publication "
                    "finalization failed"
                )

    def close(self) -> None:
        try:
            with self._connection_lock:
                self._connection.close()
        finally:
            if self._write_lock is not None:
                self._write_lock.release()
                self._write_lock = None

    def __enter__(self) -> ProjectRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _fetchone(self, sql: str, parameters: tuple | list = ()) -> sqlite3.Row | None:
        with self._connection_lock:
            return self._connection.execute(sql, parameters).fetchone()

    def _fetchall(self, sql: str, parameters: tuple | list = ()) -> list[sqlite3.Row]:
        with self._connection_lock:
            return self._connection.execute(sql, parameters).fetchall()

    def _content_revision_if_available(self) -> int | None:
        try:
            row = self._connection.execute("SELECT content_revision FROM project LIMIT 1").fetchone()
        except sqlite3.OperationalError as error:
            if "content_revision" in str(error) or "no such table" in str(error):
                return None
            raise
        return int(row["content_revision"]) if row is not None else None

    @staticmethod
    def _touch_project(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE project SET updated_at=?, content_revision=content_revision+1",
            (now_ms(),),
        )
