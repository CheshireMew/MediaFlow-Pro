from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from mediaflow.infrastructure.project_database_session import ProjectDatabaseSession


class ProjectRepositoryComponent:
    """Shared database context for one focused project persistence component."""

    def __init__(self, database: ProjectDatabaseSession):
        self._database = database

    @property
    def project_dir(self) -> Path:
        return self._database.project_dir

    @property
    def _connection(self) -> sqlite3.Connection:
        return self._database.connection

    @property
    def _connection_lock(self) -> threading.RLock:
        return self._database.connection_lock

    @property
    def transaction_depth(self) -> int:
        return self._database.transaction_depth

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]:
        return self._database.transaction()

    def content_revision(self) -> int:
        return self._database.content_revision()

    def acknowledge_content_revision(self) -> int:
        return self._database.acknowledge_content_revision()

    def available_content_revision(self) -> int | None:
        return self._database.available_content_revision()

    def project_id(self) -> str:
        return self._database.project_id()

    def _fetchone(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> sqlite3.Row | None:
        return self._database.fetchone(sql, parameters)

    def _fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> list[sqlite3.Row]:
        return self._database.fetchall(sql, parameters)

    def _touch_project(self, connection: sqlite3.Connection) -> None:
        self._database.touch_project(connection)

    def _enlist_transaction_publication(
        self,
        *,
        on_commit: Callable[[], None],
        on_rollback: Callable[[BaseException], None],
    ) -> None:
        self._database.enlist_transaction_publication(
            on_commit=on_commit,
            on_rollback=on_rollback,
        )
