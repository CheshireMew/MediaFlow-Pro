from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from mediaflow.automation.contracts import AutomationRequest
from mediaflow.automation.operation_context import project_snapshot
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.domain.collaboration import ActorIdentity

from .codec import decode_transport, encode_transport
from .commands import DesktopTarget, desktop_command
from .session_registry import ProjectSessionRegistry


class DesktopProjectOperations:
    def __init__(self, registry: ProjectSessionRegistry):
        self.registry = registry

    def project_snapshot(self, path: Path) -> dict[str, Any]:
        with self.registry.locked_session(path, require_writable=False) as session:
            project = session.project
            project_document = project.get_project()
            _tasks, task_cursor = project.task_snapshot()
            return {
                "project_id": project_document.id,
                "project_revision": project.content_revision(),
                "project_event_cursor": project.project_event_cursor(),
                "task_cursor": task_cursor,
                "snapshot": OPERATIONS["project.inspect"].validate_result(project_snapshot(project)),
            }

    def project_identity(self, path: Path) -> dict[str, Any]:
        with self.registry.leased_session(path, require_writable=False) as session:
            project = session.project
            return {
                "project": str(path),
                "project_path": str(path),
                "project_id": project.get_project().id,
                "project_revision": project.content_revision(),
            }

    def project_subscription(
        self,
        path: Path,
        *,
        project_cursor: int,
        task_cursor: int,
    ) -> dict[str, Any]:
        if project_cursor < 0 or task_cursor < 0:
            raise ValueError("event cursors must be non-negative")
        with self.registry.locked_session(path, require_writable=False) as session:
            project = session.project
            project_document = project.get_project()
            _tasks, current_task_cursor = project.task_snapshot()
            return {
                "project": str(path),
                "project_id": project_document.id,
                "project_revision": project.content_revision(),
                "project_event_cursor": project.project_event_cursor(),
                "task_cursor": current_task_cursor,
                "project_events": [
                    event.model_dump(mode="json")
                    for event in project.list_project_events(after_cursor=project_cursor)
                ],
                "task_events": [
                    self.registry.task_event_document(str(path), event)
                    for event in project.task_events_after(task_cursor)
                ],
            }

    def execute_desktop_command(
        self,
        *,
        path: Path,
        target: DesktopTarget,
        sequence_id: str,
        command: str,
        arguments_value: Any,
        base_revision: int | None,
        request_id: str,
        actor_value: dict[str, Any],
    ) -> dict[str, Any]:
        definition = desktop_command(target, command)
        request = definition.validate_arguments(decode_transport(arguments_value))
        arguments = definition.request_arguments(request)
        identity = ActorIdentity.model_validate(actor_value)
        session_scope = (
            self.registry.leased_session if definition.access == "read" else self.registry.locked_session
        )
        with session_scope(
            path,
            allow_upgrade=True,
            require_writable=definition.access != "read",
        ) as session:
            receiver: Any = session.project if target == "project" else session.project.timeline(sequence_id)

            def invoke() -> Any:
                return definition.invoke(receiver, request)

            if definition.access != "write":
                encoded = encode_transport(definition.validate_result(invoke()))
                return {
                    "value": encoded,
                    "project_revision": session.project.content_revision(),
                    "history": None,
                    "event": None,
                }
            mutation_plan = definition.mutation_plan(
                sequence_id=sequence_id,
                args=[],
                kwargs=arguments,
                project=session.project,
            )
            write_set = mutation_plan.conflict_set
            envelope = AutomationRequest(
                operation=f"desktop.{target}.{command}",
                project=str(path),
                arguments={"arguments": arguments_value},
                request_id=request_id or uuid.uuid4().hex,
                base_revision=base_revision,
                actor=identity,
                client_id=identity.id,
            )
            replayed = session.project.replay_automation_request(
                envelope.request_id,
                envelope.operation,
                envelope.arguments,
                base_revision=envelope.base_revision,
                actor=identity,
                write_set=write_set,
                undo_group_id=envelope.undo_group_id,
            )
            if replayed is not None:
                stored, event = replayed
                return {
                    "value": stored["value"],
                    "project_revision": session.project.content_revision(),
                    "rebased_from": None,
                    "history": self._history_snapshot(session.project),
                    "event_ack": self._event_ack(event),
                }
            effective, rebased_from = self.registry.resolve_revision(
                session.project,
                envelope,
                write_set,
            )
            stored, event = session.project.execute_automation_request(
                effective.request_id,
                effective.operation,
                effective.arguments,
                lambda _retrying: {
                    "value": encode_transport(definition.validate_result(invoke()))
                },
                atomic=True,
                base_revision=effective.base_revision,
                idempotency_base_revision=envelope.base_revision,
                actor=identity,
                mutation_plan=mutation_plan,
                undo_group_id=effective.undo_group_id,
                on_event=self.registry.publish_project_event,
                reversible=definition.history_mode == "reversible",
            )
            return {
                "value": stored["value"],
                "project_revision": session.project.content_revision(),
                "rebased_from": rebased_from,
                "history": self._history_snapshot(session.project),
                "event_ack": self._event_ack(event),
            }

    def project_events(self, path: Path, *, after_cursor: int = 0) -> list[dict[str, Any]]:
        with self.registry.locked_session(path, require_writable=False) as session:
            return [
                event.model_dump(mode="json")
                for event in session.project.list_project_events(after_cursor=after_cursor)
            ]

    def history_list(
        self,
        path: Path,
        *,
        include_items: bool = True,
    ) -> dict[str, Any]:
        with self.registry.locked_session(path, require_writable=False) as session:
            project = session.project
            return {
                "project_revision": project.content_revision(),
                "items": (
                    [
                        item.model_dump(mode="json", exclude_computed_fields=True)
                        for item in project.list_history()
                    ]
                    if include_items
                    else []
                ),
                "can_undo": project.can_undo,
                "can_redo": project.can_redo,
            }

    def execute_history_command(
        self,
        path: Path,
        *,
        direction: Literal["undo", "redo"],
        request_id: str,
        base_revision: int,
        actor_value: dict[str, Any],
        undo_group_id: str | None = None,
    ) -> dict[str, Any]:
        if not request_id.strip():
            raise ValueError("request_id is required")
        actor = ActorIdentity.model_validate(actor_value)
        with self.registry.locked_session(path) as session:
            before_revision = session.project.content_revision()
            result, event = session.project.execute_history_command(
                direction,
                request_id=request_id,
                base_revision=base_revision,
                actor=actor,
                undo_group_id=(undo_group_id.strip() if undo_group_id else None),
                on_event=self.registry.publish_project_event,
            )
            replayed = event.project_revision <= before_revision
            return {
                "result": result,
                "project_revision": session.project.content_revision(),
                "rebased_from": (None if replayed or base_revision == before_revision else base_revision),
                "event": event.model_dump(mode="json"),
            }

    def task_events(self, path: Path, *, after_cursor: int = 0) -> list[dict[str, Any]]:
        with self.registry.locked_session(path, require_writable=False) as session:
            return [
                self.registry.task_event_document(str(path), event)
                for event in session.project.task_events_after(after_cursor)
            ]

    @staticmethod
    def _history_snapshot(receiver: Any) -> dict[str, bool]:
        return {
            "can_undo": bool(receiver.can_undo),
            "can_redo": bool(receiver.can_redo),
        }

    @staticmethod
    def _event_ack(event: Any) -> dict[str, int] | None:
        if event is None:
            return None
        return {
            "cursor": int(event.cursor),
            "project_revision": int(event.project_revision),
        }
