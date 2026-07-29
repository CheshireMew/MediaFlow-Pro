from __future__ import annotations

import sqlite3
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaflow.infrastructure.project_repository import ProjectRepository


class ProjectRepositoryComponent:
    """Shared database context for one focused project persistence component."""

    def __init__(self, owner: ProjectRepository):
        self._owner = owner

    @property
    def project_dir(self) -> Path:
        return self._owner.project_dir

    @property
    def _connection(self) -> sqlite3.Connection:
        return self._owner._connection

    @property
    def _connection_lock(self) -> threading.RLock:
        return self._owner._connection_lock

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]:
        return self._owner.transaction()

    def content_revision(self) -> int:
        return self._owner.content_revision()

    def acknowledge_content_revision(self) -> int:
        return self._owner.acknowledge_content_revision()

    def _fetchone(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> sqlite3.Row | None:
        return self._owner._fetchone(sql, parameters)

    def _fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] | list[Any] = (),
    ) -> list[sqlite3.Row]:
        return self._owner._fetchall(sql, parameters)

    def _touch_project(self, connection: sqlite3.Connection) -> None:
        self._owner._touch_project(connection)
