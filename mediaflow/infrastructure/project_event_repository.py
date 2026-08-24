from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from mediaflow.domain.collaboration import (
    ActorIdentity,
    ProjectChange,
    ProjectChangeEvent,
    ProjectEditCommand,
)
from mediaflow.domain.model_base import new_id, now_ms

from .project_database_session import ProjectDatabaseSession
from .project_observation import ProjectObservation
from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json


@dataclass(frozen=True, slots=True)
class _ProjectChangeContext:
    operation: str
    actor: ActorIdentity
    request_id: str
    undo_group_id: str
    change_scopes: tuple[str, ...]
    before: ProjectObservation | None


class ProjectEventRepository(ProjectRepositoryComponent):
    """Durable collaboration journal and implicit-change attribution."""

    def __init__(
        self,
        database: ProjectDatabaseSession,
        *,
        observe_changes: Callable[[list[str]], ProjectObservation],
        enlist_publication: Callable[..., None],
    ) -> None:
        super().__init__(database)
        self._observe_changes = observe_changes
        self._enlist_publication = enlist_publication
        self._change_context = threading.local()
        self._implicit_observer: Callable[[ProjectChangeEvent], None] | None = None

    def observe_implicit_changes(
        self,
        observer: Callable[[ProjectChangeEvent], None] | None,
    ) -> None:
        self._implicit_observer = observer

    def has_change_scope(self) -> bool:
        return bool(getattr(self._change_context, "stack", ()))

    def publish_after_commit(self, event: ProjectChangeEvent) -> None:
        observer = self._implicit_observer
        if observer is not None:
            self._enlist_publication(
                on_commit=lambda: observer(event),
                on_rollback=lambda _error: None,
            )

    @contextmanager
    def change_scope(
        self,
        *,
        operation: str,
        actor: ActorIdentity,
        request_id: str,
        undo_group_id: str,
        write_set: list[str],
        capture_implicit_baseline: bool = True,
    ) -> Iterator[None]:
        stack = list(getattr(self._change_context, "stack", ()))
        stack.append(
            _ProjectChangeContext(
                operation=operation,
                actor=actor,
                request_id=request_id,
                undo_group_id=undo_group_id,
                change_scopes=tuple(write_set),
                before=(
                    self._observe_changes(write_set)
                    if capture_implicit_baseline
                    else None
                ),
            )
        )
        self._change_context.stack = stack
        try:
            yield
        finally:
            stack.pop()
            self._change_context.stack = stack

    def append(
        self,
        *,
        base_revision: int,
        project_revision: int,
        operation: str,
        actor: ActorIdentity,
        request_id: str,
        undo_group_id: str,
        write_set: list[str],
        changes: list[ProjectChange],
        operation_result: dict[str, Any],
        inverse_command: ProjectEditCommand | None = None,
        replace_implicit: bool = False,
    ) -> ProjectChangeEvent:
        if self.transaction_depth <= 0:
            raise RuntimeError("Project events must join the active project transaction")
        project_id = self.project_id()
        created_at = now_ms()
        if replace_implicit:
            existing = self._connection.execute(
                """SELECT cursor, operation FROM project_event
                   WHERE project_id=? AND project_revision=?""",
                (project_id, project_revision),
            ).fetchone()
            if existing is not None:
                if str(existing["operation"]) != "project.internal_change":
                    raise RuntimeError("Only an implicit migration event can be adopted by project.upgrade")
                self._connection.execute(
                    """UPDATE project_event
                       SET operation=?, actor_json=?, request_id=?, undo_group_id=?,
                           write_set_json=?, changes_json=?, result_json=?,
                           inverse_command_json=?, created_at=?
                       WHERE cursor=?""",
                    (
                        operation,
                        actor.model_dump_json(),
                        request_id,
                        undo_group_id,
                        _json(write_set),
                        _json([item.model_dump(mode="json") for item in changes]),
                        _json(operation_result),
                        (
                            inverse_command.model_dump_json(exclude_computed_fields=True)
                            if inverse_command is not None
                            else None
                        ),
                        created_at,
                        int(existing["cursor"]),
                    ),
                )
                adopted = self._fetchone(
                    """SELECT cursor, base_revision, project_revision, operation,
                              actor_json, request_id, undo_group_id, write_set_json,
                              changes_json, result_json, inverse_command_json,
                              created_at
                       FROM project_event WHERE cursor=?""",
                    (int(existing["cursor"]),),
                )
                if adopted is None:
                    raise RuntimeError("Adopted project upgrade event disappeared")
                return self._document(adopted, project_id)
        cursor = self._connection.execute(
            """INSERT INTO project_event(
                   project_id, base_revision, project_revision, operation, actor_json,
                   request_id, undo_group_id, write_set_json, changes_json,
                   result_json, inverse_command_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                base_revision,
                project_revision,
                operation,
                actor.model_dump_json(),
                request_id,
                undo_group_id,
                _json(write_set),
                _json([item.model_dump(mode="json") for item in changes]),
                _json(operation_result),
                (
                    inverse_command.model_dump_json(exclude_computed_fields=True)
                    if inverse_command is not None
                    else None
                ),
                created_at,
            ),
        ).lastrowid
        if cursor is None:
            raise RuntimeError("Project event insert did not produce a cursor")
        return ProjectChangeEvent(
            cursor=int(cursor),
            project_id=project_id,
            project_path=str(self.project_dir),
            base_revision=base_revision,
            project_revision=project_revision,
            operation=operation,
            actor=actor,
            request_id=request_id,
            undo_group_id=undo_group_id,
            write_set=write_set,
            changes=changes,
            operation_result=operation_result,
            inverse_command=inverse_command,
            created_at=created_at,
        )

    def list_events(self, *, after_cursor: int = 0) -> list[ProjectChangeEvent]:
        project_id = self.project_id()
        rows = self._fetchall(
            """SELECT cursor, base_revision, project_revision, operation, actor_json,
                      request_id, undo_group_id, write_set_json, changes_json,
                      result_json, inverse_command_json, created_at
               FROM project_event
               WHERE project_id=? AND cursor>?
               ORDER BY cursor""",
            (project_id, after_cursor),
        )
        return self._documents(rows, project_id)

    def latest_cursor(self) -> int:
        row = self._fetchone("SELECT COALESCE(MAX(cursor), 0) AS cursor FROM project_event")
        return 0 if row is None else int(row["cursor"])

    def list_after_revision(self, revision: int) -> list[ProjectChangeEvent]:
        project_id = self.project_id()
        rows = self._fetchall(
            """SELECT cursor, base_revision, project_revision, operation,
                      actor_json, request_id, undo_group_id, write_set_json,
                      changes_json, result_json, inverse_command_json, created_at
               FROM project_event
               WHERE project_id=? AND project_revision>?
               ORDER BY project_revision""",
            (project_id, revision),
        )
        return self._documents(rows, project_id)

    def for_request(self, request_id: str) -> ProjectChangeEvent | None:
        project_id = self.project_id()
        row = self._fetchone(
            """SELECT cursor, base_revision, project_revision, operation,
                      actor_json, request_id, undo_group_id, write_set_json,
                      changes_json, result_json, inverse_command_json, created_at
               FROM project_event
               WHERE project_id=? AND request_id=?""",
            (project_id, request_id),
        )
        return None if row is None else self._document(row, project_id)

    def for_undo_group(self, undo_group_id: str) -> ProjectChangeEvent | None:
        project_id = self.project_id()
        row = self._fetchone(
            """SELECT cursor, base_revision, project_revision, operation,
                      actor_json, request_id, undo_group_id, write_set_json,
                      changes_json, result_json, inverse_command_json, created_at
               FROM project_event
               WHERE project_id=? AND undo_group_id=?
               ORDER BY cursor ASC LIMIT 1""",
            (project_id, undo_group_id),
        )
        return None if row is None else self._document(row, project_id)

    def has_pending_upgrade(self) -> bool:
        return self.pending_upgrade_event() is not None

    def pending_upgrade_event(self) -> ProjectChangeEvent | None:
        project_id = self.project_id()
        row = self._fetchone(
            """SELECT cursor, base_revision, project_revision, operation,
                      actor_json, request_id, undo_group_id, write_set_json,
                      changes_json, result_json, inverse_command_json,
                      created_at
               FROM project_event
               WHERE project_id=? AND project_revision=?
                 AND operation='project.internal_change'
               ORDER BY cursor DESC LIMIT 1""",
            (project_id, self.content_revision()),
        )
        return None if row is None else self._document(row, project_id)

    def append_implicit_change(
        self,
        base_revision: int | None,
        fallback_observation: ProjectObservation | None = None,
    ) -> None:
        if base_revision is None:
            return
        project_revision = self.available_content_revision()
        if project_revision is None or project_revision == base_revision:
            return
        event_table = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_event'"
        ).fetchone()
        if event_table is None:
            return
        covered = self._connection.execute(
            "SELECT 1 FROM project_event WHERE project_revision=?",
            (project_revision,),
        ).fetchone()
        if covered is not None:
            return
        stack = getattr(self._change_context, "stack", ())
        if stack:
            context = stack[-1]
            change_scopes = list(context.change_scopes)
            before = context.before
        else:
            if fallback_observation is None:
                raise RuntimeError("A project mutation advanced the revision without an observable baseline")
            context = _ProjectChangeContext(
                operation="project.internal_change",
                actor=ActorIdentity(
                    kind="system",
                    id="editor-service",
                    name="MediaFlow Pro Editor Service",
                ),
                request_id=f"internal-{new_id()}",
                undo_group_id=f"internal-{new_id()}",
                change_scopes=tuple(fallback_observation.values),
                before=fallback_observation,
            )
            change_scopes = list(fallback_observation.values)
            before = fallback_observation
        if before is None:
            raise RuntimeError(
                f"Project mutation {context.operation!r} did not publish its explicit change event"
            )
        changes = before.changes_to(self._observe_changes(change_scopes))
        if not changes.changes:
            raise RuntimeError(
                f"Project mutation {context.operation!r} advanced the revision without an observable change"
            )
        event = self.append(
            base_revision=base_revision,
            project_revision=project_revision,
            operation=context.operation,
            actor=context.actor,
            request_id=f"{context.request_id}:{project_revision}",
            undo_group_id=context.undo_group_id,
            write_set=changes.write_set,
            changes=changes.changes,
            operation_result={},
        )
        observer = self._implicit_observer
        if observer is not None:
            self._enlist_publication(
                on_commit=lambda: observer(event),
                on_rollback=lambda _error: None,
            )

    def _documents(
        self,
        rows: Sequence[Any],
        project_id: str,
    ) -> list[ProjectChangeEvent]:
        return [self._document(row, project_id) for row in rows]

    def _document(self, row: Any, project_id: str) -> ProjectChangeEvent:
        return ProjectChangeEvent(
            cursor=int(row["cursor"]),
            project_id=project_id,
            project_path=str(self.project_dir),
            base_revision=int(row["base_revision"]),
            project_revision=int(row["project_revision"]),
            operation=str(row["operation"]),
            actor=ActorIdentity.model_validate_json(str(row["actor_json"])),
            request_id=str(row["request_id"]),
            undo_group_id=str(row["undo_group_id"]),
            write_set=json.loads(str(row["write_set_json"])),
            changes=[ProjectChange.model_validate(item) for item in json.loads(str(row["changes_json"]))],
            operation_result=json.loads(str(row["result_json"])),
            inverse_command=(
                ProjectEditCommand.model_validate_json(str(row["inverse_command_json"]))
                if row["inverse_command_json"] is not None
                else None
            ),
            created_at=int(row["created_at"]),
        )
