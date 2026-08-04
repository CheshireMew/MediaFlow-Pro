from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, cast

from mediaflow.domain.collaboration import (
    ActiveUndoGroupState,
    ActorIdentity,
    ProjectEditCommand,
    ProjectUndoGroup,
    UndoGroupState,
)
from mediaflow.domain.model_base import now_ms

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json


class ProjectHistoryRepository(ProjectRepositoryComponent):
    """Durable undo groups owned by the project transaction boundary."""

    def record_group(
        self,
        *,
        group_id: str,
        source_revision: int,
        label: str,
        actor: ActorIdentity,
        write_set: list[str],
        command: ProjectEditCommand,
    ) -> ProjectUndoGroup:
        if self._owner._transaction_depth <= 0:
            raise RuntimeError("Undo groups must join the active project transaction")
        project = self._owner.catalog.get_project()
        timestamp = now_ms()
        self._connection.execute(
            """UPDATE undo_group
               SET state='discarded', updated_at=?
               WHERE project_id=? AND state='undone'""",
            (timestamp, project.id),
        )
        self._connection.execute(
            """INSERT INTO undo_group(
                   id, project_id, source_revision, state_revision, label,
                   actor_json, write_set_json, command_json, state,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?)""",
            (
                group_id,
                project.id,
                source_revision,
                source_revision,
                label,
                actor.model_dump_json(),
                _json(write_set),
                command.model_dump_json(exclude_computed_fields=True),
                timestamp,
                timestamp,
            ),
        )
        return ProjectUndoGroup(
            id=group_id,
            project_id=project.id,
            source_revision=source_revision,
            state_revision=source_revision,
            label=label,
            actor=actor,
            write_set=write_set,
            command=command,
            state="applied",
            created_at=timestamp,
            updated_at=timestamp,
        )

    def discard_redo(self) -> None:
        if self._owner._transaction_depth <= 0:
            raise RuntimeError("Redo invalidation must join the active project transaction")
        project = self._owner.catalog.get_project()
        self._connection.execute(
            """UPDATE undo_group
               SET state='discarded', updated_at=?
               WHERE project_id=? AND state='undone'""",
            (now_ms(), project.id),
        )

    def get(self, group_id: str) -> ProjectUndoGroup | None:
        project = self._owner.catalog.get_project()
        row = self._fetchone(
            """SELECT id, project_id, source_revision, state_revision, label,
                      actor_json, write_set_json, command_json, state,
                      created_at, updated_at
               FROM undo_group WHERE project_id=? AND id=?""",
            (project.id, group_id),
        )
        return None if row is None else self._document(row)

    def list_groups(
        self,
        *,
        include_discarded: bool = False,
    ) -> list[ProjectUndoGroup]:
        project = self._owner.catalog.get_project()
        rows = self._fetchall(
            """SELECT id, project_id, source_revision, state_revision, label,
                      actor_json, write_set_json, command_json, state,
                      created_at, updated_at
               FROM undo_group
               WHERE project_id=? AND (? OR state!='discarded')
               ORDER BY source_revision, created_at, id""",
            (project.id, int(include_discarded)),
        )
        return self._documents(rows)

    def latest_applied(self) -> ProjectUndoGroup | None:
        return self._latest("applied")

    def latest_undone(self) -> ProjectUndoGroup | None:
        return self._latest("undone")

    def has_applied(self) -> bool:
        return self._has_state("applied")

    def has_undone(self) -> bool:
        return self._has_state("undone")

    def transition(
        self,
        group_id: str,
        *,
        expected: ActiveUndoGroupState,
        state: ActiveUndoGroupState,
        state_revision: int,
    ) -> ProjectUndoGroup:
        if self._owner._transaction_depth <= 0:
            raise RuntimeError("Undo state transitions must join the project transaction")
        project = self._owner.catalog.get_project()
        changed = self._connection.execute(
            """UPDATE undo_group
               SET state=?, state_revision=?, updated_at=?
               WHERE project_id=? AND id=? AND state=?""",
            (
                state,
                state_revision,
                now_ms(),
                project.id,
                group_id,
                expected,
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError(
                f"Undo group {group_id!r} is no longer {expected}"
            )
        group = self.get(group_id)
        if group is None:
            raise RuntimeError("Transitioned undo group disappeared")
        return group

    def _latest(
        self,
        state: Literal["applied", "undone"],
    ) -> ProjectUndoGroup | None:
        project = self._owner.catalog.get_project()
        row = self._fetchone(
            """SELECT id, project_id, source_revision, state_revision, label,
                      actor_json, write_set_json, command_json, state,
                      created_at, updated_at
               FROM undo_group
               WHERE project_id=? AND state=?
               ORDER BY state_revision DESC, updated_at DESC, id DESC
               LIMIT 1""",
            (project.id, state),
        )
        return None if row is None else self._document(row)

    def _has_state(
        self,
        state: Literal["applied", "undone"],
    ) -> bool:
        project = self._owner.catalog.get_project()
        return self._fetchone(
            "SELECT 1 FROM undo_group WHERE project_id=? AND state=? LIMIT 1",
            (project.id, state),
        ) is not None

    def _documents(self, rows: Sequence[Any]) -> list[ProjectUndoGroup]:
        return [self._document(row) for row in rows]

    @staticmethod
    def _document(row: Any) -> ProjectUndoGroup:
        return ProjectUndoGroup(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            source_revision=int(row["source_revision"]),
            state_revision=int(row["state_revision"]),
            label=str(row["label"]),
            actor=ActorIdentity.model_validate_json(str(row["actor_json"])),
            write_set=json.loads(str(row["write_set_json"])),
            command=ProjectEditCommand.model_validate_json(str(row["command_json"])),
            state=cast(UndoGroupState, str(row["state"])),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )
