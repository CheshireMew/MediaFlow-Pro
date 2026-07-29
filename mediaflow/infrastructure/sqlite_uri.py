from __future__ import annotations

from pathlib import Path


def read_only_database_uri(database_path: str | Path) -> str:
    """Return an encoded SQLite URI for one existing read-only database."""

    path = Path(database_path).expanduser().resolve()
    return f"{path.as_uri()}?mode=ro"
