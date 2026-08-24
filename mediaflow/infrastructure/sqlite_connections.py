from __future__ import annotations

import sqlite3
from pathlib import Path

from .sqlite_uri import read_only_database_uri

PROJECT_DATABASE_TIMEOUT_SECONDS = 5.0
PROJECT_DATABASE_BUSY_TIMEOUT_MS = 5_000


def open_project_database(
    database_path: str | Path,
    *,
    read_only: bool,
    check_same_thread: bool = True,
    durable_writes: bool = False,
) -> sqlite3.Connection:
    """Open one MediaFlow project database with the shared connection contract."""

    connection: sqlite3.Connection | None = None
    try:
        if read_only:
            connection = sqlite3.connect(
                read_only_database_uri(database_path),
                uri=True,
                timeout=PROJECT_DATABASE_TIMEOUT_SECONDS,
                check_same_thread=check_same_thread,
            )
        else:
            connection = sqlite3.connect(
                database_path,
                timeout=PROJECT_DATABASE_TIMEOUT_SECONDS,
                check_same_thread=check_same_thread,
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={PROJECT_DATABASE_BUSY_TIMEOUT_MS}")
        if durable_writes:
            if read_only:
                raise ValueError("Durable write pragmas require a writable project database")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
        return connection
    except BaseException:
        if connection is not None:
            connection.close()
        raise
