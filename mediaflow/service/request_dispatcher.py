from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from mediaflow.automation.contracts import AutomationRequest, describe_contract
from mediaflow.domain.collaboration import ActorIdentity

from .commands import parse_desktop_target
from .discovery import SERVICE_PROTOCOL, SERVICE_PROTOCOL_VERSION
from .events import EventHub, ServiceEvent
from .session_registry import project_path
from .sessions import EditorServiceOperations
from .workspaces import WorkspaceRegistry

_UNHANDLED = object()
DispatchGroup = Callable[[str, dict[str, Any]], Awaitable[Any]]


class ServiceRequestDispatcher:
    """Routes public JSON-RPC methods to focused service boundaries."""

    def __init__(
        self,
        operations: EditorServiceOperations,
        workspaces: WorkspaceRegistry | None,
        events: EventHub | None,
        request_stop: Callable[[], None],
    ) -> None:
        self._operations = operations
        self._workspaces = workspaces
        self._events = events
        self._request_stop = request_stop
        self._groups: tuple[DispatchGroup, ...] = (
            self._dispatch_system,
            self._dispatch_automation,
            self._dispatch_project,
            self._dispatch_desktop,
            self._dispatch_history_and_events,
            self._dispatch_tasks,
            self._dispatch_workspace,
        )

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        for group in self._groups:
            result = await group(method, params)
            if result is not _UNHANDLED:
                return result
        raise ValueError(f"Unknown JSON-RPC method: {method}")

    async def _dispatch_system(self, method: str, params: dict[str, Any]) -> Any:
        if method == "system.hello":
            return {
                "protocol": SERVICE_PROTOCOL,
                "protocol_version": SERVICE_PROTOCOL_VERSION,
                "pid": os.getpid(),
            }
        if method == "system.describe":
            return describe_contract(params)
        if method == "system.runtime.inspect":
            return await asyncio.to_thread(
                self._operations.runtime.desktop_runtime_descriptor
            )
        if method == "service.status":
            status = await asyncio.to_thread(self._operations.service_status)
            return {
                "protocol": SERVICE_PROTOCOL,
                "protocol_version": SERVICE_PROTOCOL_VERSION,
                "pid": os.getpid(),
                **status,
            }
        if method == "service.shutdown":
            force = params.get("force", False)
            if not isinstance(force, bool):
                raise ValueError("force must be a boolean")
            result = await asyncio.to_thread(
                self._operations.prepare_shutdown,
                force=force,
            )
            asyncio.get_running_loop().call_soon(self._request_stop)
            return result
        return _UNHANDLED

    async def _dispatch_automation(self, method: str, params: dict[str, Any]) -> Any:
        if method == "operation.execute":
            value = params.get("request")
            if not isinstance(value, dict):
                raise ValueError("params.request must be an object")
            return await asyncio.to_thread(self._operations.automation.execute, value)
        if method == "operation.execute_batch":
            values = params.get("requests")
            if not isinstance(values, list) or not all(
                isinstance(item, dict) for item in values
            ):
                raise ValueError("params.requests must be an array of request objects")
            return await asyncio.to_thread(
                self._operations.automation.execute_batch,
                values,
                batch_id=str(params.get("batch_id") or ""),
                label=str(params.get("label") or "Agent batch"),
            )
        return _UNHANDLED

    async def _dispatch_project(self, method: str, params: dict[str, Any]) -> Any:
        if method not in {
            "project.create",
            "project.open",
            "project.close",
            "project.snapshot",
            "project.subscribe",
        }:
            return _UNHANDLED
        path = project_path(str(params.get("project") or ""))
        client_id = str(params.get("client_id") or "")
        if method == "project.create":
            name = str(params.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            profile_confirmed = params.get("profile_confirmed")
            if not isinstance(profile_confirmed, bool):
                raise ValueError("profile_confirmed must be a boolean")
            return await asyncio.to_thread(
                self._operations.registry.create_desktop_project,
                path,
                name,
                params.get("profile"),
                profile_confirmed,
                client_id,
            )
        if method == "project.open":
            return await asyncio.to_thread(
                self._operations.registry.open_desktop_project,
                path,
                client_id,
            )
        if method == "project.close":
            return await asyncio.to_thread(
                self._operations.registry.release_desktop_project,
                path,
                client_id,
                float(params.get("timeout_seconds", 5.0)),
            )
        if method == "project.snapshot":
            return await asyncio.to_thread(self._operations.desktop.project_snapshot, path)
        return await asyncio.to_thread(
            self._operations.desktop.project_subscription,
            path,
            project_cursor=int(params.get("project_cursor", 0)),
            task_cursor=int(params.get("task_cursor", 0)),
        )

    async def _dispatch_desktop(self, method: str, params: dict[str, Any]) -> Any:
        if method == "desktop.project.call":
            path = project_path(str(params.get("project") or ""))
            raw_actor = params.get("actor")
            actor_value: dict[str, Any] = raw_actor if isinstance(raw_actor, dict) else {}
            return await asyncio.to_thread(
                self._operations.desktop.execute_desktop_command,
                path=path,
                target=parse_desktop_target(params.get("target")),
                sequence_id=str(params.get("sequence_id") or ""),
                command=str(params.get("command") or ""),
                args_value=params.get("args", []),
                kwargs_value=params.get("kwargs", {}),
                base_revision=(
                    int(params["base_revision"])
                    if params.get("base_revision") is not None
                    else None
                ),
                request_id=str(params.get("request_id") or ""),
                actor_value=actor_value,
            )
        runtime = self._operations.runtime
        if method == "desktop.application.settings":
            return await asyncio.to_thread(runtime.application_settings)
        if method == "desktop.application.settings.replace":
            return await asyncio.to_thread(
                runtime.replace_application_settings,
                params.get("settings"),
            )
        if method == "desktop.application.cookies":
            return await asyncio.to_thread(
                runtime.cookie_command,
                str(params.get("command") or ""),
                params.get("args", []),
            )
        if method == "desktop.application.call":
            return await asyncio.to_thread(
                runtime.execute_application_command,
                str(params.get("command") or ""),
                params.get("args", []),
                params.get("kwargs", {}),
            )
        return _UNHANDLED

    async def _dispatch_history_and_events(
        self,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        if method == "history.list":
            path = project_path(str(params.get("project") or ""))
            return await asyncio.to_thread(self._operations.desktop.history_list, path)
        if method in {"history.undo", "history.redo"}:
            if params.get("base_revision") is None:
                raise ValueError("base_revision is required")
            raw_actor = params.get("actor")
            if not isinstance(raw_actor, dict):
                raise ValueError("actor must be an object")
            return await asyncio.to_thread(
                self._operations.desktop.execute_history_command,
                project_path(str(params.get("project") or "")),
                direction="undo" if method == "history.undo" else "redo",
                request_id=str(params.get("request_id") or ""),
                base_revision=int(params["base_revision"]),
                actor_value=raw_actor,
                undo_group_id=(
                    str(params["undo_group_id"])
                    if params.get("undo_group_id") is not None
                    else None
                ),
            )
        if method not in {"project.events", "task.events"}:
            return _UNHANDLED
        after_cursor = int(params.get("after_cursor", 0))
        if after_cursor < 0:
            raise ValueError("after_cursor must be non-negative")
        boundary = (
            self._operations.desktop.project_events
            if method == "project.events"
            else self._operations.desktop.task_events
        )
        return await asyncio.to_thread(
            boundary,
            project_path(str(params.get("project") or "")),
            after_cursor=after_cursor,
        )

    async def _dispatch_tasks(self, method: str, params: dict[str, Any]) -> Any:
        if method not in {"task.get", "task.list", "task.cancel", "task.wait"}:
            return _UNHANDLED
        arguments = (
            {}
            if method == "task.list"
            else {
                "task_id": str(params.get("task_id") or ""),
                **(
                    {"timeout": float(params.get("timeout", 3600))}
                    if method == "task.wait"
                    else {}
                ),
            }
        )
        request = AutomationRequest(
            operation=method,
            project=str(project_path(str(params.get("project") or ""))),
            arguments=arguments,
            request_id=params.get("request_id"),
            base_revision=params.get("base_revision"),
            actor=ActorIdentity.model_validate(
                params.get("actor")
                or {
                    "kind": "system",
                    "id": "editor-service",
                    "name": "Editor Service",
                }
            ),
            client_id=str(params.get("client_id") or "editor-service"),
        )
        return await asyncio.to_thread(self._operations.automation.execute, request)

    async def _dispatch_workspace(self, method: str, params: dict[str, Any]) -> Any:
        if not method.startswith("workspace."):
            return _UNHANDLED
        if self._workspaces is None:
            raise RuntimeError("Workspace registry is not ready")
        if method == "workspace.attach":
            return self._workspaces.attach(
                client_id=str(params.get("client_id") or ""),
                project=(str(params["project"]) if params.get("project") else None),
                workspace_session_id=(
                    str(params["workspace_session_id"])
                    if params.get("workspace_session_id")
                    else None
                ),
            )
        if method == "workspace.list":
            return {
                "workspaces": self._workspaces.list(
                    project=(str(params["project"]) if params.get("project") else None),
                    connected_only=bool(params.get("connected_only", True)),
                )
            }
        if method == "workspace.status":
            return self._workspaces.status(
                str(params.get("workspace_session_id") or "")
            )
        if method == "workspace.command":
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError("workspace command arguments must be an object")
            event = self._workspaces.command(
                str(params.get("workspace_session_id") or ""),
                str(params.get("command") or ""),
                arguments,
            )
            if self._events is None:
                raise RuntimeError("Editor Service events are not ready")
            self._events.publish(ServiceEvent("workspace.changed", event))
            return event
        if method == "workspace.detach":
            self._workspaces.detach(
                str(params.get("workspace_session_id") or ""),
                str(params.get("client_id") or ""),
            )
            return {"detached": True}
        return _UNHANDLED
