from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediaflow.infrastructure.project_repository_relations import (
    ProjectRepositoryRelations,
)


@dataclass(frozen=True, slots=True)
class ProjectDatabaseSession:
    """Narrow shared SQL boundary used by project repository components."""

    project_dir: Path
    database_path: Path
    read_only: bool
    relations: ProjectRepositoryRelations
    connection: sqlite3.Connection
    connection_lock: threading.RLock
    transaction_factory: Callable[[], AbstractContextManager[sqlite3.Connection]]
    content_revision_reader: Callable[[], int]
    content_revision_acknowledger: Callable[[], int]
    transaction_depth_reader: Callable[[], int]
    available_content_revision_reader: Callable[[], int | None]
    project_touch: Callable[[sqlite3.Connection], None]
    publication_enlister: Callable[..., None]

    @property
    def transaction_depth(self) -> int:
        return self.transaction_depth_reader()

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]:
        return self.transaction_factory()

    def content_revision(self) -> int:
        return self.content_revision_reader()

    def acknowledge_content_revision(self) -> int:
        return self.content_revision_acknowledger()

    def available_content_revision(self) -> int | None:
        return self.available_content_revision_reader()

    def project_id(self) -> str:
        row = self.fetchone("SELECT id FROM project LIMIT 1")
        if row is None:
            raise RuntimeError("Project identity is not initialized")
        return str(row["id"])

    def fetchone(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> sqlite3.Row | None:
        with self.connection_lock:
            return self.connection.execute(sql, parameters).fetchone()

    def fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> list[sqlite3.Row]:
        with self.connection_lock:
            return self.connection.execute(sql, parameters).fetchall()

    def touch_project(self, connection: sqlite3.Connection) -> None:
        self.project_touch(connection)

    def enlist_transaction_publication(
        self,
        *,
        on_commit: Callable[[], None],
        on_rollback: Callable[[BaseException], None],
    ) -> None:
        self.publication_enlister(
            on_commit=on_commit,
            on_rollback=on_rollback,
        )
