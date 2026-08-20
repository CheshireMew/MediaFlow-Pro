from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.project_revision_policy import resolve_project_revision
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.collaboration import (
    ActiveUndoGroupState,
    ActorIdentity,
    ProjectChangeEvent,
    ProjectChangeSet,
    ProjectEditCommand,
    ProjectMutationPlan,
    ProjectUndoGroup,
    project_write_path_covers,
)
from mediaflow.infrastructure.project_repository import ProjectRepository

DEFAULT_IDEMPOTENCY_BASE = object()


@dataclass(frozen=True, slots=True)
class AutomationBatchCommand:
    request_id: str
    operation: str
    arguments: dict[str, Any]
    actor: ActorIdentity
    write_set: list[str]
    change_scopes: list[str]
    action: Callable[[], dict[str, Any]]


class ProjectCollaboration:
    """Own durable request receipts, conflict checks, events, undo and redo."""

    def __init__(
        self,
        repository: ProjectRepository,
        history: ProjectEditHistory,
        timeline_provider: Callable[[str], TimelineEditor],
        reload_timelines: Callable[[], None],
    ) -> None:
        self._repository = repository
        self._history = history
        self._timeline_provider = timeline_provider
        self._reload_timelines = reload_timelines

    @property
    def can_undo(self) -> bool:
        return self._repository.history.has_applied()

    @property
    def can_redo(self) -> bool:
        return self._repository.history.has_undone()

    def list_history(self) -> list[ProjectUndoGroup]:
        return self._repository.history.list_groups()

    def history_target(
        self,
        direction: Literal["undo", "redo"],
        *,
        undo_group_id: str | None = None,
    ) -> ProjectUndoGroup:
        group = (
            self._repository.history.get(undo_group_id)
            if undo_group_id
            else (
                self._repository.history.latest_applied()
                if direction == "undo"
                else self._repository.history.latest_undone()
            )
        )
        expected_state = "applied" if direction == "undo" else "undone"
        if group is None or group.state != expected_state:
            raise RuntimeError(f"Nothing to {direction}")
        return group

    def execute_history_command(
        self,
        direction: Literal["undo", "redo"],
        *,
        request_id: str,
        base_revision: int,
        actor: ActorIdentity,
        undo_group_id: str | None = None,
        on_event: Callable[[ProjectChangeEvent], None] | None = None,
    ) -> tuple[dict[str, Any], ProjectChangeEvent]:
        input_hash = automation_request_input_hash(
            arguments={
                "direction": direction,
                "undo_group_id": undo_group_id,
            },
            base_revision=base_revision,
            actor=actor,
            write_set=[],
            undo_group_id=undo_group_id,
        )
        operation = f"history.{direction}"
        with self._repository.transaction():
            cached = self._repository.operations.result(
                request_id,
                operation,
                input_hash,
            )
            if cached is not None:
                event = self._repository.events.for_request(request_id)
                if event is None:
                    raise RuntimeError("Persisted history request has no project event")
                return cached, event
            before_revision = self._repository.content_revision()
            group = self.history_target(
                direction,
                undo_group_id=undo_group_id,
            )
            self._raise_if_write_set_conflicts(
                start_revision=base_revision,
                current_revision=before_revision,
                write_set=group.write_set,
                reason=("one or more fields changed after the requested base revision"),
            )
            self._ensure_history_command_handlers(group.command)
            self._raise_if_history_conflicts(
                group,
                current_revision=before_revision,
            )
            before_observation = self._repository.observations.capture(group.write_set)
            actions = group.command.undo_actions if direction == "undo" else group.command.redo_actions
            with self._repository.coalesced_revision():
                self._history.apply_actions(actions)
            after_revision = self._repository.content_revision()
            if after_revision != before_revision + 1:
                raise RuntimeError(f"history.{direction} must advance exactly one revision")
            changes = before_observation.changes_to(self._repository.observations.capture(group.write_set))
            if not changes.changes:
                raise RuntimeError(f"history.{direction} produced no observable project changes")
            state: ActiveUndoGroupState = "undone" if direction == "undo" else "applied"
            transitioned = self._repository.history.transition(
                group.id,
                expected=cast(ActiveUndoGroupState, group.state),
                state=state,
                state_revision=after_revision,
            )
            result = {
                "direction": direction,
                "undo_group": transitioned.model_dump(
                    mode="json",
                    exclude_computed_fields=True,
                ),
                "can_undo": (self._repository.history.latest_applied() is not None),
                "can_redo": (self._repository.history.latest_undone() is not None),
            }
            stored = self._repository.operations.save_result(
                request_id,
                operation,
                input_hash,
                result,
            )
            event = self._repository.events.append(
                base_revision=before_revision,
                project_revision=after_revision,
                operation=operation,
                actor=actor,
                request_id=request_id,
                undo_group_id=group.id,
                write_set=changes.write_set,
                changes=changes.changes,
                operation_result=stored,
                inverse_command=group.command,
            )
            if on_event is not None:
                self._repository.enlist_transaction_publication(
                    on_commit=lambda: on_event(event),
                    on_rollback=lambda _error: None,
                )
            return stored, event

    def execute_batch(
        self,
        commands: list[AutomationBatchCommand],
        *,
        batch_id: str,
        label: str,
        base_revision: int,
        idempotency_base_revision: int,
        on_event: Callable[[ProjectChangeEvent], None] | None = None,
    ) -> tuple[list[dict[str, Any]], ProjectChangeEvent]:
        if not commands:
            raise ValueError("Automation batch must contain at least one command")
        checkpoint = self._history.checkpoint()
        try:
            batch_scopes = sorted({path for command in commands for path in command.change_scopes})
            with (
                self._repository.events.change_scope(
                    operation="operation.execute_batch",
                    actor=commands[0].actor,
                    request_id=batch_id,
                    undo_group_id=batch_id,
                    write_set=batch_scopes,
                ),
                self._repository.transaction(),
            ):
                before_revision = self._repository.content_revision()
                if base_revision != before_revision:
                    raise RuntimeError(
                        f"Project revision conflict: expected {base_revision}, current {before_revision}"
                    )
                results: list[dict[str, Any]] = []
                with self._repository.coalesced_revision():
                    for command in commands:
                        input_hash = automation_request_input_hash(
                            arguments=command.arguments,
                            base_revision=idempotency_base_revision,
                            actor=command.actor,
                            write_set=command.write_set,
                            undo_group_id=batch_id,
                        )
                        cached = self._repository.operations.result(
                            command.request_id,
                            command.operation,
                            input_hash,
                        )
                        if cached is not None:
                            raise RuntimeError(
                                "Atomic collaboration batch contains an already completed request"
                            )
                        result = command.action()
                        results.append(
                            self._repository.operations.save_result(
                                command.request_id,
                                command.operation,
                                input_hash,
                                result,
                            )
                        )
                after_revision = self._repository.content_revision()
                if after_revision != before_revision + 1:
                    raise RuntimeError("Atomic collaboration batch must advance exactly one revision")
                durable_command = self._history.combined_since(
                    checkpoint,
                    label=label,
                )
                if durable_command is None:
                    raise RuntimeError("Atomic collaboration batch did not produce an inverse command")
                planned_change_scopes = sorted(
                    {path for command in commands for path in command.change_scopes}
                )
                change_set = self._history.change_set_since(checkpoint)
                require_planned_changes(
                    "operation.execute_batch",
                    planned_change_scopes,
                    change_set,
                )
                combined_write_set = change_set.write_set
                if not combined_write_set:
                    raise RuntimeError("Atomic collaboration batch produced no observable changes")
                self._history.squash_since(checkpoint, label=label)
                self._repository.history.record_group(
                    group_id=batch_id,
                    source_revision=after_revision,
                    label=durable_command.label,
                    actor=commands[0].actor,
                    write_set=combined_write_set,
                    command=durable_command,
                )
                event_result = {
                    "batch_id": batch_id,
                    "results": [
                        {
                            "request_id": command.request_id,
                            "result": result,
                        }
                        for command, result in zip(
                            commands,
                            results,
                            strict=True,
                        )
                    ],
                }
                event = self._repository.events.append(
                    base_revision=before_revision,
                    project_revision=after_revision,
                    operation="operation.execute_batch",
                    actor=commands[0].actor,
                    request_id=batch_id,
                    undo_group_id=batch_id,
                    write_set=combined_write_set,
                    changes=change_set.changes,
                    operation_result=event_result,
                    inverse_command=durable_command,
                )
                if on_event is not None:
                    self._repository.enlist_transaction_publication(
                        on_commit=lambda: on_event(event),
                        on_rollback=lambda _error: None,
                    )
                return results, event
        except BaseException:
            self._history.restore(checkpoint)
            self._reload_timelines()
            raise

    def execute_request(
        self,
        request_id: str | None,
        operation: str,
        arguments: dict[str, Any],
        action: Callable[[bool], dict[str, Any]],
        *,
        atomic: bool,
        base_revision: int | None = None,
        idempotency_base_revision: int | None | object = DEFAULT_IDEMPOTENCY_BASE,
        actor: ActorIdentity,
        mutation_plan: ProjectMutationPlan,
        undo_group_id: str | None = None,
        on_event: Callable[[ProjectChangeEvent], None] | None = None,
        force_event: bool = False,
        reversible: bool = False,
    ) -> tuple[dict[str, Any], ProjectChangeEvent | None]:
        if not request_id:
            return action(False), None
        input_hash = automation_request_input_hash(
            arguments=arguments,
            base_revision=(
                base_revision
                if idempotency_base_revision is DEFAULT_IDEMPOTENCY_BASE
                else cast(int | None, idempotency_base_revision)
            ),
            actor=actor,
            write_set=mutation_plan.conflict_set,
            undo_group_id=undo_group_id,
        )
        if not atomic:
            cached, retrying = self._repository.operations.begin(
                request_id,
                operation,
                input_hash,
            )
            if cached is not None:
                return cached, None
            return self._repository.operations.save_result(
                request_id,
                operation,
                input_hash,
                action(retrying),
            ), None
        history_checkpoint = self._history.checkpoint()
        try:
            with (
                self._repository.events.change_scope(
                    operation=operation,
                    actor=actor,
                    request_id=request_id,
                    undo_group_id=undo_group_id or request_id,
                    write_set=mutation_plan.change_scopes,
                ),
                self._repository.transaction(),
            ):
                cached = self._repository.operations.result(
                    request_id,
                    operation,
                    input_hash,
                )
                if cached is not None:
                    return cached, self._repository.events.for_request(request_id)
                before_revision = self._repository.content_revision()
                if base_revision is None:
                    raise ValueError("base_revision is required for project writes")
                if base_revision != before_revision:
                    raise RuntimeError(
                        f"Project revision conflict: expected {base_revision}, current {before_revision}"
                    )
                before_observation = self._repository.observations.capture(mutation_plan.change_scopes)
                result = action(False)
                command = self._history.combined_since(history_checkpoint) if reversible else None
                history_change_set = (
                    self._history.change_set_since(history_checkpoint) if reversible else ProjectChangeSet()
                )
                observed_change_set = before_observation.changes_to(
                    self._repository.observations.capture(mutation_plan.change_scopes)
                )
                change_set = history_change_set if reversible else observed_change_set
                stored = self._repository.operations.save_result(
                    request_id,
                    operation,
                    input_hash,
                    result,
                )
                after_revision = self._repository.content_revision()
                if reversible and after_revision != before_revision and command is None:
                    raise RuntimeError(
                        f"Reversible operation {operation!r} did not produce an inverse command"
                    )
                if reversible and after_revision != before_revision:
                    if not change_set.changes:
                        raise RuntimeError(
                            f"Reversible operation {operation!r} produced no observable change set"
                        )
                    require_planned_changes(
                        operation,
                        mutation_plan.change_scopes,
                        change_set,
                    )
                if after_revision != before_revision and not observed_change_set.changes:
                    raise RuntimeError(
                        f"Operation {operation!r} advanced the project revision without an observable change"
                    )
                if not reversible and after_revision != before_revision:
                    require_planned_changes(
                        operation,
                        mutation_plan.change_scopes,
                        change_set,
                    )
                event = None
                if force_event or after_revision != before_revision:
                    group_id = undo_group_id or request_id
                    if command is None:
                        self._repository.history.discard_redo()
                        pending_upgrade = (
                            self._repository.events.pending_upgrade_event()
                            if operation == "project.upgrade"
                            else None
                        )
                        event_write_set = (
                            pending_upgrade.write_set if pending_upgrade is not None else change_set.write_set
                        )
                        event_changes = (
                            pending_upgrade.changes if pending_upgrade is not None else change_set.changes
                        )
                    else:
                        event_write_set = change_set.write_set
                        event_changes = change_set.changes
                        self._repository.history.record_group(
                            group_id=group_id,
                            source_revision=after_revision,
                            label=command.label,
                            actor=actor,
                            write_set=event_write_set,
                            command=command,
                        )
                    event = self._repository.events.append(
                        base_revision=before_revision,
                        project_revision=after_revision,
                        operation=operation,
                        actor=actor,
                        request_id=request_id,
                        undo_group_id=group_id,
                        write_set=event_write_set,
                        changes=event_changes,
                        operation_result=result,
                        inverse_command=command,
                        replace_implicit=operation == "project.upgrade",
                    )
                    if on_event is not None:
                        self._repository.enlist_transaction_publication(
                            on_commit=lambda: on_event(event),
                            on_rollback=lambda _error: None,
                        )
                return stored, event
        except BaseException:
            self._history.restore(history_checkpoint)
            self._reload_timelines()
            raise

    def replay_request(
        self,
        request_id: str | None,
        operation: str,
        arguments: dict[str, Any],
        *,
        base_revision: int | None,
        actor: ActorIdentity,
        write_set: list[str],
        undo_group_id: str | None = None,
    ) -> tuple[dict[str, Any], ProjectChangeEvent | None] | None:
        if not request_id:
            return None
        input_hash = automation_request_input_hash(
            arguments=arguments,
            base_revision=base_revision,
            actor=actor,
            write_set=write_set,
            undo_group_id=undo_group_id,
        )
        result = self._repository.operations.result(
            request_id,
            operation,
            input_hash,
        )
        if result is None:
            return None
        return result, self._repository.events.for_request(request_id)

    def request_is_running(
        self,
        request_id: str | None,
        operation: str,
        arguments: dict[str, Any],
        *,
        base_revision: int | None,
        actor: ActorIdentity,
        write_set: list[str],
        undo_group_id: str | None = None,
    ) -> bool:
        if not request_id:
            return False
        input_hash = automation_request_input_hash(
            arguments=arguments,
            base_revision=base_revision,
            actor=actor,
            write_set=write_set,
            undo_group_id=undo_group_id,
        )
        return self._repository.operations.is_running(
            request_id,
            operation,
            input_hash,
        )

    def _ensure_history_command_handlers(
        self,
        command: ProjectEditCommand,
    ) -> None:
        for action in (*command.undo_actions, *command.redo_actions):
            prefix = "timeline.restore:"
            if action.kind.startswith(prefix):
                sequence_id = action.kind.removeprefix(prefix).strip()
                if not sequence_id:
                    raise RuntimeError("Persisted timeline action has no sequence id")
                self._timeline_provider(sequence_id)

    def _raise_if_history_conflicts(
        self,
        group: ProjectUndoGroup,
        *,
        current_revision: int,
    ) -> None:
        self._raise_if_write_set_conflicts(
            start_revision=group.state_revision,
            current_revision=current_revision,
            write_set=group.write_set,
            reason="one or more fields changed after the undo target",
        )

    def _raise_if_write_set_conflicts(
        self,
        *,
        start_revision: int,
        current_revision: int,
        write_set: list[str],
        reason: str,
    ) -> None:
        resolve_project_revision(
            base_revision=start_revision,
            current_revision=current_revision,
            write_set=write_set,
            events=self._repository.events.list_after_revision(start_revision),
            conflict_reason=reason,
        )


def automation_request_input_hash(
    *,
    arguments: dict[str, Any],
    base_revision: int | None,
    actor: ActorIdentity,
    write_set: list[str],
    undo_group_id: str | None,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "arguments": arguments,
                "base_revision": base_revision,
                "actor": actor.model_dump(mode="json"),
                "write_set": write_set,
                "undo_group_id": undo_group_id,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def require_planned_changes(
    operation: str,
    planned_write_set: list[str],
    changes: ProjectChangeSet,
) -> None:
    outside_plan = [
        path
        for path in changes.write_set
        if not any(project_write_path_covers(scope, path) for scope in planned_write_set)
    ]
    if outside_plan:
        raise RuntimeError(f"Operation {operation!r} changed paths outside its mutation plan: {outside_plan}")
