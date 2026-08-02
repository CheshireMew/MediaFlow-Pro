from __future__ import annotations

import sqlite3
from typing import Any, NoReturn

from .project_migrations import MIGRATION_BY_SOURCE_VERSION
from .project_schema_definition import PROJECT_SCHEMA_VERSION


class ProjectUpgradeRequiredError(RuntimeError):
    def __init__(self, version: int, target_version: int):
        self.version = version
        self.target_version = target_version
        super().__init__(
            "Project requires a writable one-time upgrade from schema "
            f"{version} to {target_version}; run project.upgrade or open it "
            "in the desktop editor"
        )


class ProjectSchemaMigrator:
    def __init__(self, workspace: Any):
        self.workspace = workspace

    def validate(self) -> None:
        version = self._read_version()
        if version == PROJECT_SCHEMA_VERSION:
            return
        if (
            self.workspace.read_only
            and version is not None
            and version < PROJECT_SCHEMA_VERSION
            and version in MIGRATION_BY_SOURCE_VERSION
        ):
            raise ProjectUpgradeRequiredError(
                version,
                PROJECT_SCHEMA_VERSION,
            )
        if self.workspace.read_only:
            self._raise_unsupported(version)
        with self.workspace.transaction():
            self._migrate(version)

    def _migrate(self, version: int | None) -> None:
        while version != PROJECT_SCHEMA_VERSION:
            if version is None:
                self._raise_unsupported(version)
            migration = MIGRATION_BY_SOURCE_VERSION.get(version)
            if migration is None:
                self._raise_unsupported(version)
            migration.apply(self.workspace)
            migrated_version = self._read_version()
            if migrated_version != migration.target_version:
                raise RuntimeError(
                    "Project migration did not advance from "
                    f"{migration.source_version} to {migration.target_version}"
                )
            version = migrated_version

    def _read_version(self) -> int | None:
        try:
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        except sqlite3.Error as error:
            raise RuntimeError(f"Invalid MediaFlow Pro project: {self.workspace.database_path}") from error
        return None if row is None else int(row["version"])

    @staticmethod
    def _raise_unsupported(version: int | None) -> NoReturn:
        raise RuntimeError(f"Unsupported project schema: {version}")
