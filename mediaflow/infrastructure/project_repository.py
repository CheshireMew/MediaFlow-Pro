from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
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

from .editable_media_project_migration import (
    reconcile_editable_media_v4_archives,
)
from .project_database_session import ProjectDatabaseSession
from .project_lock import ProcessFileLock
from .project_migration_runner import ProjectSchemaMigrator
from .project_repository_assembly import assemble_project_repositories
from .project_schema_definition import (
    MANAGED_DIRECTORIES,
    PROJECT_FILE_NAME,
    PROJECT_SCHEMA_VERSION,
    SCHEMA_SQL,
)
from .sqlite_connections import open_project_database

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
        write_lock: ProcessFileLock | None,
    ):
        self.project_dir = project_dir
        self.database_path = project_dir / PROJECT_FILE_NAME
        self._connection = connection
        self._connection_lock = threading.RLock()
        self._task_preparation_state = threading.local()
        self._mutation_gate: Any | None = None
        self._transaction_depth = 0
        self._transaction_observes_project = True
        self._revision_coalescing_depth = 0
        self._revision_dirty = False
        self._savepoint_serial = 0
        self._transaction_publications: list[_TransactionPublication] = []
        self.read_only = read_only
        self._write_lock = write_lock
        self._known_content_revision: int | None = None
        self._database = ProjectDatabaseSession(
            project_dir=self.project_dir,
            database_path=self.database_path,
            read_only=self.read_only,
            connection=self._connection,
            connection_lock=self._connection_lock,
            transaction_factory=lambda: self.transaction(),
            content_revision_reader=self.content_revision,
            content_revision_acknowledger=self.acknowledge_content_revision,
            transaction_depth_reader=lambda: self._transaction_depth,
            available_content_revision_reader=self._content_revision_if_available,
            project_touch=self._touch_project,
            publication_enlister=self.enlist_transaction_publication,
        )
        components = assemble_project_repositories(self._database)
        self.projects = components.projects
        self.sequences = components.sequences
        self.assets = components.assets
        self.observations = components.observations
        self.events = components.events
        self.history = components.history
        self.operations = components.operations
        self.timeline = components.timeline
        self.frame_clock = components.frame_clock
        self.audio = components.audio
        self.dubbing = components.dubbing
        self.subtitles = components.subtitles
        self.highlights = components.highlights
        self.web = components.web
        self.records = components.records

    def _bind_mutation_gate(self, gate: Any) -> None:
        """Route every writable transaction through the owning project session."""

        if self.read_only:
            raise PermissionError("A read-only project cannot bind a mutation gate")
        if self._transaction_depth:
            raise RuntimeError("Cannot bind a mutation gate during a transaction")
        if self._mutation_gate is not None and self._mutation_gate is not gate:
            raise RuntimeError("Project repository already belongs to another mutation gate")
        self._mutation_gate = gate

    @contextmanager
    def _task_preparation_scope(self, task_id: str) -> Iterator[None]:
        previous = getattr(self._task_preparation_state, "task_id", None)
        self._task_preparation_state.task_id = task_id
        try:
            yield
        finally:
            self._task_preparation_state.task_id = previous

    @contextmanager
    def _task_project_command(self) -> Iterator[None]:
        previous = getattr(self._task_preparation_state, "task_id", None)
        self._task_preparation_state.task_id = None
        try:
            yield
        finally:
            self._task_preparation_state.task_id = previous

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

        lock = ProcessFileLock(root / "cache" / "project.lock")
        if not lock.acquire():
            raise RuntimeError(f"Project directory is already locked: {root}")
        repository: ProjectRepository | None = None
        published = False
        try:
            connection = open_project_database(
                temporary_path,
                read_only=False,
                check_same_thread=False,
                durable_writes=True,
            )
            repository = cls(
                root,
                connection,
                read_only=False,
                write_lock=lock,
            )
            repository._initialize(
                name=name,
                profile=profile or ProjectProfile(),
                profile_confirmed=profile is not None,
            )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                raise RuntimeError("New project database failed its integrity check")
            ProjectSchemaMigrator(repository).validate()
            project = repository.projects.get_project()
            repository.sequences.get_sequence(project.main_sequence_id)
            repository.acknowledge_content_revision()
            with repository._connection_lock:
                repository._connection.close()
            repository._write_lock = None
            repository = None
            if database_path.exists():
                raise FileExistsError(f"Project already exists: {database_path}")
            temporary_path.rename(database_path)
            published = True
            final_connection = open_project_database(
                database_path,
                read_only=False,
                check_same_thread=False,
                durable_writes=True,
            )
            repository = cls(
                root,
                final_connection,
                read_only=False,
                write_lock=lock,
            )
            repository.acknowledge_content_revision()
            return repository
        except BaseException as error:
            try:
                if repository is not None:
                    repository.close()
                else:
                    lock.release()
            except BaseException as cleanup_error:
                error.add_note(f"创建项目失败后的资源释放失败：{cleanup_error}")
            failed_path = database_path if published else temporary_path
            try:
                cls._archive_failed_creation(root, failed_path)
            except BaseException as archive_error:
                error.add_note(f"创建失败数据库归档失败：{archive_error}")
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
        migration_chromium: Path | None = None,
    ) -> ProjectRepository:
        root = require_project_root_path(project_dir).resolve(strict=True)
        database_path = root / PROJECT_FILE_NAME
        if not database_path.is_file():
            raise FileNotFoundError(database_path)
        lock: ProcessFileLock | None = None
        read_only = not writable
        if writable:
            candidate = ProcessFileLock(root / "cache" / "project.lock")
            if candidate.acquire():
                lock = candidate
            else:
                read_only = True
        connection: sqlite3.Connection | None = None
        repository: ProjectRepository | None = None
        try:
            connection = open_project_database(
                database_path,
                read_only=read_only,
                check_same_thread=False,
                durable_writes=not read_only,
            )
            repository = cls(
                root,
                connection,
                read_only=read_only,
                write_lock=lock,
            )
            ProjectSchemaMigrator(
                repository,
                chromium=migration_chromium,
            ).validate()
            reconcile_editable_media_v4_archives(repository)
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
                error.add_note(f"打开项目失败后的资源释放失败：{cleanup_error}")
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
            self.sequences._insert_sequence_record(connection, sequence)
            for bus in (master_bus, dialogue_bus, music_bus, effects_bus):
                self.audio.insert_bus_record(connection, bus)

    @contextmanager
    def transaction(self, *, observe_project: bool = True) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise PermissionError("Project is open read-only")
        with self._mutation_gate or nullcontext(), self._connection_lock:
            if self._transaction_depth:
                if observe_project and not self._transaction_observes_project:
                    raise RuntimeError(
                        "Project content cannot be changed inside a task-state transaction"
                    )
                publication_start = len(self._transaction_publications)
                self._savepoint_serial += 1
                savepoint = f"mediaflow_nested_{self._transaction_depth}_{self._savepoint_serial}"
                self._connection.execute(f"SAVEPOINT {savepoint}")
                self._transaction_depth += 1
                try:
                    yield self._connection
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                except BaseException as error:
                    try:
                        self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                    except BaseException as rollback_error:
                        error.add_note(f"嵌套项目事务回滚失败：{rollback_error}")
                    self._rollback_publications(
                        publication_start,
                        error,
                    )
                    raise
                finally:
                    self._transaction_depth -= 1
                return
            if self._transaction_publications:
                raise RuntimeError("Project transaction publication state leaked from a previous transaction")
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._transaction_depth = 1
                self._transaction_observes_project = observe_project
                transaction_base_revision = self._content_revision_if_available()
                fallback_observation = None
                if (
                    observe_project
                    and transaction_base_revision is not None
                    and not self.events.has_change_scope()
                ):
                    fallback_observation = (
                        self.observations.capture(["/project"])
                        if self._schema_is_current()
                        else self.observations.capture_schema_upgrade_baseline()
                    )
                if self._known_content_revision is not None:
                    current_revision = self.content_revision()
                    if current_revision != self._known_content_revision:
                        raise RuntimeError(
                            "Project content changed in another process; reload before editing"
                        )
                yield self._connection
                if observe_project:
                    self.events.append_implicit_change(
                        transaction_base_revision,
                        fallback_observation,
                    )
                final_content_revision = (
                    self._content_revision_if_available()
                    if observe_project
                    else transaction_base_revision
                )
                self._connection.commit()
                # The mutation gate and the project process lock exclude another
                # writer between the in-transaction read and this commit. Reuse
                # that value instead of forcing a second durable-file read after
                # every command.
                self._known_content_revision = final_content_revision
            except BaseException as error:
                try:
                    self._connection.rollback()
                except BaseException as rollback_error:
                    error.add_note(f"项目数据库回滚失败：{rollback_error}")
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
                self._transaction_observes_project = True
                self._transaction_publications.clear()

    def task_transaction(self) -> AbstractContextManager[sqlite3.Connection]:
        """Persist task lifecycle state without observing or revising project content."""

        return self.transaction(observe_project=False)

    def enlist_transaction_publication(
        self,
        *,
        on_commit: Callable[[], None],
        on_rollback: Callable[[BaseException], None],
    ) -> None:
        if self._transaction_depth <= 0:
            raise RuntimeError("File publication must join an active project transaction")
        self._transaction_publications.append(
            _TransactionPublication(
                on_commit=on_commit,
                on_rollback=on_rollback,
            )
        )

    @contextmanager
    def coalesced_revision(self) -> Iterator[None]:
        """Collapse all domain touches in one outer transaction to one revision."""

        if self._transaction_depth <= 0:
            raise RuntimeError("Revision coalescing requires an active project transaction")
        outer = self._revision_coalescing_depth == 0
        if outer:
            self._revision_dirty = False
        self._revision_coalescing_depth += 1
        succeeded = False
        try:
            yield
            succeeded = True
        finally:
            self._revision_coalescing_depth -= 1
            if outer:
                try:
                    if succeeded and self._revision_dirty:
                        self._connection.execute(
                            """UPDATE project
                               SET updated_at=?, content_revision=content_revision+1""",
                            (now_ms(),),
                        )
                finally:
                    self._revision_dirty = False

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
                error.add_note(f"项目文件发布回滚失败：{rollback_error}")

    def _commit_publications(self) -> None:
        publications = tuple(self._transaction_publications)
        self._transaction_publications.clear()
        for publication in publications:
            try:
                publication.on_commit()
            except BaseException:
                logger.exception("Project transaction committed, but publication finalization failed")

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

    def _fetchone(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> sqlite3.Row | None:
        with self._connection_lock:
            row: sqlite3.Row | None = self._connection.execute(sql, parameters).fetchone()
            return row

    def _fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> list[sqlite3.Row]:
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

    def _schema_is_current(self) -> bool:
        try:
            row = self._connection.execute(
                "SELECT version FROM schema_info WHERE component='project'"
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None and int(row["version"]) == PROJECT_SCHEMA_VERSION

    def _touch_project(self, connection: sqlite3.Connection) -> None:
        if not self._transaction_observes_project:
            raise RuntimeError("Task-state transactions cannot change project content")
        task_id = getattr(self._task_preparation_state, "task_id", None)
        if task_id is not None:
            raise RuntimeError(f"后台任务准备阶段不能直接修改项目；任务 {task_id} 必须延迟到完成命令提交")
        if self._revision_coalescing_depth:
            self._revision_dirty = True
            connection.execute(
                "UPDATE project SET updated_at=?",
                (now_ms(),),
            )
            return
        connection.execute(
            "UPDATE project SET updated_at=?, content_revision=content_revision+1",
            (now_ms(),),
        )
