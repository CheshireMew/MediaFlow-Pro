from __future__ import annotations

from functools import partial
from typing import Any

from mediaflow.automation.contracts import AutomationRequest, describe_contract
from mediaflow.automation.executor import execute_operation
from mediaflow.automation.operation_context import OperationContext
from mediaflow.automation.operation_registry import OPERATIONS, OperationDefinition
from mediaflow.project_collaboration import AutomationBatchCommand

from .project_paths import project_path
from .session_registry import ProjectSession, ProjectSessionRegistry


class ProjectAutomationOperations:
    def __init__(self, registry: ProjectSessionRegistry):
        self.registry = registry

    def execute(self, value: dict[str, Any] | AutomationRequest) -> dict[str, Any]:
        envelope = value if isinstance(value, AutomationRequest) else AutomationRequest.model_validate(value)
        operation = envelope.operation.strip()
        if operation == "describe":
            return {
                "result": describe_contract(envelope.arguments),
                "project_revision": None,
                "event": None,
            }
        definition = OPERATIONS.get(operation)
        if definition is None:
            raise ValueError(f"Unknown operation: {operation}")
        envelope = envelope.model_copy(
            update={
                "operation": operation,
                "arguments": definition.validate_arguments(envelope.arguments),
            }
        )
        if definition.project_access == "none":
            result = definition.validate_result(
                definition.handler(OperationContext(None, self.registry.application, envelope))
            )
            return {"result": result, "project_revision": None, "event": None}
        if definition.project_access in {"create", "write"} and not envelope.request_id:
            raise ValueError("request_id is required for project writes")
        leased_session = (
            self.registry.leased_created_session(envelope)
            if definition.project_access == "create"
            else self.registry.leased_session(
                project_path(envelope.project),
                allow_upgrade=envelope.operation == "project.upgrade",
                require_writable=definition.project_access == "write",
            )
        )
        with leased_session as session:
            if envelope.operation == "task.wait":
                # Waiting must not own the project mutation gate: the worker
                # needs that same gate to publish its terminal project result.
                result = definition.validate_result(
                    definition.handler(OperationContext(session.project, self.registry.application, envelope))
                )
                with session.write_lock:
                    revision = session.project.content_revision()
                return {
                    "result": result,
                    "project_revision": revision,
                    "rebased_from": None,
                    "event": None,
                }
            if definition.execution_mode == "task":
                # Task handlers may wait while the worker reaches an admitted
                # state. Keep the session alive, but release the foreground
                # gate so independent edits remain responsive.
                with session.write_lock:
                    prepared, request_base_revision, rebased_from, replay = self._prepare_execution(
                        session, envelope, definition
                    )
                if replay is not None:
                    return replay
                return self._execute_prepared(
                    session,
                    prepared,
                    request_base_revision=request_base_revision,
                    rebased_from=rebased_from,
                )
            with session.write_lock:
                prepared, request_base_revision, rebased_from, replay = self._prepare_execution(
                    session, envelope, definition
                )
                if replay is not None:
                    return replay
                return self._execute_prepared(
                    session,
                    prepared,
                    request_base_revision=request_base_revision,
                    rebased_from=rebased_from,
                )

    def _prepare_execution(
        self,
        session: ProjectSession,
        envelope: AutomationRequest,
        definition: OperationDefinition,
    ) -> tuple[AutomationRequest, int | None, int | None, dict[str, Any] | None]:
        rebased_from = None
        request_base_revision = envelope.base_revision
        if definition.project_access != "write":
            return envelope, request_base_revision, rebased_from, None
        mutation_plan = definition.mutation_plan(
            envelope.operation,
            envelope.arguments,
            session.project,
        )
        replayed = session.project.replay_automation_request(
            envelope.request_id,
            envelope.operation,
            envelope.arguments,
            base_revision=envelope.base_revision,
            actor=envelope.actor,
            write_set=mutation_plan.conflict_set,
            undo_group_id=envelope.undo_group_id,
        )
        if replayed is not None:
            result, event = replayed
            return (
                envelope,
                request_base_revision,
                rebased_from,
                {
                    "result": definition.validate_result(result),
                    "project_revision": session.project.content_revision(),
                    "rebased_from": None,
                    "event": (event.model_dump(mode="json") if event is not None else None),
                },
            )
        running_retry = session.project.automation_request_is_running(
            envelope.request_id,
            envelope.operation,
            envelope.arguments,
            base_revision=envelope.base_revision,
            actor=envelope.actor,
            write_set=mutation_plan.conflict_set,
            undo_group_id=envelope.undo_group_id,
        )
        if envelope.operation == "project.upgrade":
            current_revision = session.project.content_revision()
            if envelope.base_revision != current_revision:
                rebased_from = envelope.base_revision
                envelope = envelope.model_copy(update={"base_revision": current_revision})
        elif running_retry:
            current_revision = session.project.content_revision()
            if envelope.base_revision != current_revision:
                rebased_from = envelope.base_revision
                envelope = envelope.model_copy(update={"base_revision": current_revision})
        else:
            envelope, rebased_from = self.registry.resolve_revision(
                session.project,
                envelope,
                mutation_plan.conflict_set,
            )
        return envelope, request_base_revision, rebased_from, None

    def _execute_prepared(
        self,
        session: ProjectSession,
        envelope: AutomationRequest,
        *,
        request_base_revision: int | None,
        rebased_from: int | None,
    ) -> dict[str, Any]:
        result, event = execute_operation(
            session.project,
            self.registry.application,
            envelope,
            request_base_revision=request_base_revision,
            on_event=self.registry.publish_project_event,
        )
        return {
            "result": result,
            "project_revision": session.project.content_revision(),
            "rebased_from": rebased_from,
            "event": event.model_dump(mode="json") if event is not None else None,
        }

    def execute_batch(
        self,
        values: list[dict[str, Any]],
        *,
        batch_id: str,
        label: str,
    ) -> dict[str, Any]:
        if not values:
            raise ValueError("A collaboration batch must contain at least one request")
        if not batch_id.strip():
            raise ValueError("batch_id is required")
        envelopes: list[AutomationRequest] = []
        definitions = []
        for value in values:
            envelope = AutomationRequest.model_validate(value)
            operation = envelope.operation.strip()
            definition = OPERATIONS.get(operation)
            if definition is None:
                raise ValueError(f"Unknown operation: {operation}")
            if definition.project_access != "write" or definition.execution_mode != "atomic":
                raise ValueError(f"{operation} cannot join an atomic collaboration batch")
            if definition.history_mode != "reversible":
                raise ValueError(f"{operation} is non_undoable and cannot join an atomic batch")
            if not envelope.request_id:
                raise ValueError("Every batch request requires request_id")
            envelopes.append(
                envelope.model_copy(
                    update={
                        "operation": operation,
                        "arguments": definition.validate_arguments(envelope.arguments),
                        "undo_group_id": batch_id,
                    }
                )
            )
            definitions.append(definition)
        paths = {project_path(envelope.project) for envelope in envelopes}
        if len(paths) != 1:
            raise ValueError("Every batch request must target the same project")
        actors = {envelope.actor.model_dump_json() for envelope in envelopes}
        clients = {envelope.client_id for envelope in envelopes}
        if len(actors) != 1 or len(clients) != 1:
            raise ValueError("Every batch request must use the same actor and client_id")
        with self.registry.locked_session(paths.pop()) as session:
            mutation_plans = [
                definition.mutation_plan(
                    envelope.operation,
                    envelope.arguments,
                    session.project,
                )
                for envelope, definition in zip(envelopes, definitions, strict=True)
            ]
            replayed_batch = [
                session.project.replay_automation_request(
                    envelope.request_id,
                    envelope.operation,
                    envelope.arguments,
                    base_revision=envelope.base_revision,
                    actor=envelope.actor,
                    write_set=mutation_plan.conflict_set,
                    undo_group_id=batch_id,
                )
                for envelope, mutation_plan in zip(
                    envelopes,
                    mutation_plans,
                    strict=True,
                )
            ]
            if all(item is not None for item in replayed_batch):
                batch_event = session.project.project_event_for_undo_group(batch_id)
                if batch_event is None or batch_event.operation != "operation.execute_batch":
                    raise RuntimeError("Atomic collaboration batch receipt has no durable batch event")
                replay_results = []
                for envelope, definition, replayed in zip(
                    envelopes,
                    definitions,
                    replayed_batch,
                    strict=True,
                ):
                    assert replayed is not None
                    result, _event = replayed
                    replay_results.append(
                        {
                            "request_id": envelope.request_id,
                            "result": definition.validate_result(result),
                        }
                    )
                return {
                    "batch_id": batch_id,
                    "results": replay_results,
                    "project_revision": session.project.content_revision(),
                    "rebased_from": None,
                    "event": batch_event.model_dump(mode="json"),
                }
            if any(item is not None for item in replayed_batch):
                raise RuntimeError("Atomic collaboration batch has only a partial durable receipt")
            expected = envelopes[0].base_revision
            if expected is None:
                raise ValueError("Every batch request requires base_revision")
            if any(envelope.base_revision != expected for envelope in envelopes):
                raise ValueError("Every batch request must use the same base_revision")
            combined_write_set = sorted(
                {path for mutation_plan in mutation_plans for path in mutation_plan.conflict_set}
            )
            first, rebased_from = self.registry.resolve_revision(
                session.project,
                envelopes[0],
                combined_write_set,
            )
            first_base_revision = first.base_revision
            if first_base_revision is None:
                raise RuntimeError("Resolved atomic batch has no base revision")
            commands: list[AutomationBatchCommand] = []
            for envelope, definition, mutation_plan in zip(
                envelopes,
                definitions,
                mutation_plans,
                strict=True,
            ):
                request_id = envelope.request_id
                if request_id is None:
                    raise RuntimeError("Validated atomic batch has no request_id")
                commands.append(
                    AutomationBatchCommand(
                        request_id=request_id,
                        operation=envelope.operation,
                        arguments=envelope.arguments,
                        actor=envelope.actor,
                        write_set=mutation_plan.conflict_set,
                        change_scopes=mutation_plan.change_scopes,
                        action=partial(
                            self._execute_atomic_batch_item,
                            session,
                            envelope,
                            definition,
                            base_revision=first_base_revision,
                            batch_id=batch_id,
                        ),
                    )
                )
            command_results, event = session.project.execute_automation_batch(
                commands,
                batch_id=batch_id,
                label=label.strip() or "Agent batch",
                base_revision=first_base_revision,
                idempotency_base_revision=expected,
                on_event=self.registry.publish_project_event,
            )
            results = [
                {"request_id": command.request_id, "result": result}
                for command, result in zip(commands, command_results, strict=True)
            ]
            return {
                "batch_id": batch_id,
                "results": results,
                "project_revision": session.project.content_revision(),
                "rebased_from": rebased_from,
                "event": event.model_dump(mode="json"),
            }

    def _execute_atomic_batch_item(
        self,
        session: ProjectSession,
        envelope: AutomationRequest,
        definition: OperationDefinition,
        *,
        base_revision: int,
        batch_id: str,
    ) -> dict[str, Any]:
        effective = envelope.model_copy(
            update={
                "base_revision": base_revision,
                "undo_group_id": batch_id,
            }
        )
        return definition.validate_result(
            definition.handler(
                OperationContext(
                    session.project,
                    self.registry.application,
                    effective,
                )
            )
        )
