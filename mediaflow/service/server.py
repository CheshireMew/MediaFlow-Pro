from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil
from aiohttp import WSMsgType, web
from pydantic import ValidationError

from mediaflow.automation.contracts import describe_contract
from mediaflow.composition import EditorApplication
from mediaflow.domain.collaboration import ProjectRevisionConflict
from mediaflow.infrastructure.project_lock import ProcessFileLock

from .discovery import (
    SERVICE_PROTOCOL,
    SERVICE_PROTOCOL_VERSION,
    ServiceDiscovery,
    ServicePaths,
)
from .events import EventHub, ServiceEvent
from .sessions import ProjectSessionManager, project_path
from .workspaces import WorkspaceRegistry

JSON_RPC_VERSION = "2.0"
PRIVATE_PORT_START = 49_152
PRIVATE_PORT_END = 65_535
PORT_BIND_ATTEMPTS = 128

logger = logging.getLogger(__name__)


def _bind_loopback_listener() -> socket.socket:
    """Bind a stable private-use port instead of accepting low Windows ports."""

    last_error: OSError | None = None
    for _attempt in range(PORT_BIND_ATTEMPTS):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = PRIVATE_PORT_START + secrets.randbelow(
            PRIVATE_PORT_END - PRIVATE_PORT_START + 1
        )
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen(socket.SOMAXCONN)
            listener.setblocking(False)
            return listener
        except OSError as error:
            last_error = error
            listener.close()
    raise RuntimeError(
        "MediaFlow Editor Service could not reserve a private loopback port"
    ) from last_error


def _json_rpc_error(identifier: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSON_RPC_VERSION, "id": identifier, "error": error}


class EditorServiceServer:
    def __init__(
        self,
        *,
        paths: ServicePaths | None = None,
        application_factory: Callable[[], EditorApplication] = EditorApplication,
    ):
        self.paths = paths or ServicePaths.discover()
        self._application_factory = application_factory
        self._instance_lock = ProcessFileLock(self.paths.lock)
        self._runner: web.AppRunner | None = None
        self._site: web.SockSite | None = None
        self._socket: socket.socket | None = None
        self._stop = asyncio.Event()
        self._event_hub: EventHub | None = None
        self._sessions: ProjectSessionManager | None = None
        self._workspaces: WorkspaceRegistry | None = None
        self.discovery: ServiceDiscovery | None = None

    async def start(self) -> ServiceDiscovery:
        self.paths.prepare()
        if not self._instance_lock.acquire():
            raise RuntimeError("Another MediaFlow Editor Service instance already owns this user session")
        try:
            self.paths.archive_discovery()
            loop = asyncio.get_running_loop()
            self._event_hub = EventHub(loop)
            self._workspaces = WorkspaceRegistry()
            # Task recovery and every other persistent mutation happens only
            # after the per-user service lock is held.
            application = self._application_factory()
            self._sessions = ProjectSessionManager(application, self._event_hub)
            app = web.Application(
                middlewares=[self._authenticate],
                client_max_size=8 * 1024 * 1024,
            )
            app.router.add_get("/health", self._health)
            app.router.add_post("/rpc", self._rpc)
            app.router.add_get("/events", self._websocket)
            self._runner = web.AppRunner(app, access_log=None)
            await self._runner.setup()
            listener = _bind_loopback_listener()
            self._socket = listener
            port = int(listener.getsockname()[1])
            self._site = web.SockSite(self._runner, listener)
            await self._site.start()
            process = psutil.Process(os.getpid())
            self.discovery = ServiceDiscovery(
                pid=process.pid,
                process_started_at=process.create_time(),
                started_at=time.time(),
                port=port,
                token=secrets.token_urlsafe(32),
            )
            self.discovery.write(self.paths.discovery)
            return self.discovery
        except BaseException:
            await self.stop()
            raise

    async def serve(self) -> None:
        if self.discovery is None:
            await self.start()
        await self._stop.wait()

    async def stop(self) -> None:
        stop_error: BaseException | None = None
        discovery = self.discovery
        if self._event_hub is not None:
            self._event_hub.publish(ServiceEvent("service.stopping", {}))
        try:
            if self._runner is not None:
                await self._runner.cleanup()
                self._runner = None
            if self._sessions is not None:
                try:
                    await asyncio.to_thread(self._sessions.close)
                except BaseException as error:
                    stop_error = error
                finally:
                    self._sessions = None
            if self._workspaces is not None:
                self._workspaces.close()
                self._workspaces = None
            try:
                from mediaflow.infrastructure.web_capture_engine import (
                    shutdown_web_capture_engines,
                )

                await asyncio.to_thread(shutdown_web_capture_engines)
            except BaseException as error:
                if stop_error is None:
                    stop_error = error
        finally:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            if discovery is not None:
                self.paths.archive_discovery(expected_pid=discovery.pid)
            self.discovery = None
            self._instance_lock.release()
        if stop_error is not None:
            raise stop_error

    def request_stop(self) -> None:
        self._stop.set()

    @web.middleware
    async def _authenticate(self, request: web.Request, handler):
        discovery = self.discovery
        if discovery is None:
            return web.json_response({"error": "service_not_ready"}, status=503)
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {discovery.token}"
        if not secrets.compare_digest(supplied, expected):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "protocol": SERVICE_PROTOCOL,
                "protocol_version": SERVICE_PROTOCOL_VERSION,
                "pid": os.getpid(),
            }
        )

    async def _rpc(self, request: web.Request) -> web.Response:
        identifier: Any = None
        method = ""
        params: dict[str, Any] = {}
        try:
            payload = await request.json(loads=json.loads)
            if not isinstance(payload, dict):
                raise ValueError("JSON-RPC request must be an object")
            identifier = payload.get("id")
            if payload.get("jsonrpc") != JSON_RPC_VERSION:
                raise ValueError("jsonrpc must be '2.0'")
            raw_method = payload.get("method")
            if not isinstance(raw_method, str) or not raw_method:
                raise ValueError("method is required")
            method = raw_method
            params = payload.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            result = await self._dispatch(method, params)
            response = {"jsonrpc": JSON_RPC_VERSION, "id": identifier, "result": result}
        except (ValueError, ValidationError) as error:
            response = _json_rpc_error(
                identifier, -32602, str(error), {"type": type(error).__name__}
            )
        except FileNotFoundError as error:
            response = _json_rpc_error(
                identifier, -32004, str(error), {"type": type(error).__name__}
            )
        except PermissionError as error:
            response = _json_rpc_error(
                identifier, -32003, str(error), {"type": type(error).__name__}
            )
        except ProjectRevisionConflict as error:
            try:
                await self._publish_project_conflict(method, params, error)
            except Exception:
                logger.exception("Failed to publish the project conflict event")
            response = _json_rpc_error(
                identifier, -32009, str(error), error.as_dict()
            )
        except RuntimeError as error:
            response = _json_rpc_error(
                identifier, -32000, str(error), {"type": type(error).__name__}
            )
        except Exception as error:
            response = _json_rpc_error(
                identifier, -32603, str(error), {"type": type(error).__name__}
            )
        return web.json_response(response)

    async def _publish_project_conflict(
        self,
        method: str,
        params: dict[str, Any],
        error: ProjectRevisionConflict,
    ) -> None:
        sessions = self._sessions
        events = self._event_hub
        if sessions is None or events is None:
            return
        context = self._conflict_request_context(method, params)
        project_value = str(context.get("project") or "")
        if not project_value:
            return
        identity = await asyncio.to_thread(
            sessions.project_identity,
            project_path(project_value),
        )
        events.publish(
            ServiceEvent(
                "project.conflict",
                {
                    **error.as_dict(),
                    **context,
                    **identity,
                },
            )
        )

    @staticmethod
    def _conflict_request_context(
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        source: dict[str, Any]
        operation = method
        request_id = str(params.get("request_id") or "")
        if method == "operation.execute":
            raw = params.get("request")
            source = raw if isinstance(raw, dict) else {}
            operation = str(source.get("operation") or method)
            request_id = str(source.get("request_id") or "")
        elif method == "operation.execute_batch":
            raw_requests = params.get("requests")
            source = (
                raw_requests[0]
                if isinstance(raw_requests, list)
                and raw_requests
                and isinstance(raw_requests[0], dict)
                else {}
            )
            request_id = str(params.get("batch_id") or "")
        else:
            source = params
            if method == "desktop.project.call":
                target = str(params.get("target") or "project")
                command = str(params.get("command") or "")
                operation = f"desktop.{target}.{command}" if command else method
        actor = source.get("actor")
        return {
            "project": str(source.get("project") or params.get("project") or ""),
            "request_id": request_id,
            "actor": actor if isinstance(actor, dict) else {},
            "operation": operation,
        }

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        sessions = self._sessions
        if sessions is None:
            raise RuntimeError("Editor Service is not ready")
        if method == "system.hello":
            return {
                "protocol": SERVICE_PROTOCOL,
                "protocol_version": SERVICE_PROTOCOL_VERSION,
                "pid": os.getpid(),
            }
        if method == "system.describe":
            return describe_contract()
        if method == "system.runtime.inspect":
            return await asyncio.to_thread(sessions.desktop_runtime_descriptor)
        if method == "service.status":
            status = await asyncio.to_thread(sessions.service_status)
            return {
                "protocol": SERVICE_PROTOCOL,
                "protocol_version": SERVICE_PROTOCOL_VERSION,
                "pid": os.getpid(),
                **status,
            }
        if method == "operation.execute":
            value = params.get("request")
            if not isinstance(value, dict):
                raise ValueError("params.request must be an object")
            return await asyncio.to_thread(sessions.execute, value)
        if method == "operation.execute_batch":
            values = params.get("requests")
            if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
                raise ValueError("params.requests must be an array of request objects")
            batch_id = str(params.get("batch_id") or "")
            label = str(params.get("label") or "Agent batch")
            return await asyncio.to_thread(
                sessions.execute_batch,
                values,
                batch_id=batch_id,
                label=label,
            )
        if method == "project.create":
            path = project_path(str(params.get("project") or ""))
            name = str(params.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            profile_confirmed = params.get("profile_confirmed")
            if not isinstance(profile_confirmed, bool):
                raise ValueError("profile_confirmed must be a boolean")
            return await asyncio.to_thread(
                sessions.create_desktop_project,
                path,
                name,
                params.get("profile"),
                profile_confirmed,
                str(params.get("client_id") or ""),
            )
        if method == "project.open":
            path = project_path(str(params.get("project") or ""))
            return await asyncio.to_thread(
                sessions.open_desktop_project,
                path,
                str(params.get("client_id") or ""),
            )
        if method == "project.close":
            path = project_path(str(params.get("project") or ""))
            return await asyncio.to_thread(
                sessions.release_desktop_project,
                path,
                str(params.get("client_id") or ""),
                float(params.get("timeout_seconds", 5.0)),
            )
        if method == "project.snapshot":
            path = project_path(str(params.get("project") or ""))
            return await asyncio.to_thread(sessions.project_snapshot, path)
        if method == "project.subscribe":
            path = project_path(str(params.get("project") or ""))
            return await asyncio.to_thread(
                sessions.project_subscription,
                path,
                project_cursor=int(params.get("project_cursor", 0)),
                task_cursor=int(params.get("task_cursor", 0)),
            )
        if method == "desktop.project.call":
            path = project_path(str(params.get("project") or ""))
            raw_actor = params.get("actor")
            actor_value: dict[str, Any] = raw_actor if isinstance(raw_actor, dict) else {}
            return await asyncio.to_thread(
                sessions.execute_desktop_command,
                path=path,
                target=str(params.get("target") or ""),
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
        if method == "desktop.application.settings":
            return await asyncio.to_thread(sessions.application_settings)
        if method == "desktop.application.settings.replace":
            return await asyncio.to_thread(
                sessions.replace_application_settings,
                params.get("settings"),
            )
        if method == "desktop.application.cookies":
            return await asyncio.to_thread(
                sessions.cookie_command,
                str(params.get("command") or ""),
                params.get("args", []),
            )
        if method == "desktop.application.call":
            return await asyncio.to_thread(
                sessions.execute_application_command,
                str(params.get("command") or ""),
                params.get("args", []),
                params.get("kwargs", {}),
            )
        if method == "history.list":
            path = project_path(str(params.get("project") or ""))
            return await asyncio.to_thread(sessions.history_list, path)
        if method in {"history.undo", "history.redo"}:
            path = project_path(str(params.get("project") or ""))
            if params.get("base_revision") is None:
                raise ValueError("base_revision is required")
            raw_actor = params.get("actor")
            if not isinstance(raw_actor, dict):
                raise ValueError("actor must be an object")
            return await asyncio.to_thread(
                sessions.execute_history_command,
                path,
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
        if method == "project.events":
            path = project_path(str(params.get("project") or ""))
            after_cursor = int(params.get("after_cursor", 0))
            if after_cursor < 0:
                raise ValueError("after_cursor must be non-negative")
            return await asyncio.to_thread(
                sessions.project_events,
                path,
                after_cursor=after_cursor,
            )
        if method == "task.events":
            path = project_path(str(params.get("project") or ""))
            after_cursor = int(params.get("after_cursor", 0))
            if after_cursor < 0:
                raise ValueError("after_cursor must be non-negative")
            return await asyncio.to_thread(
                sessions.task_events,
                path,
                after_cursor=after_cursor,
            )
        if method in {"task.get", "task.list", "task.cancel", "task.wait"}:
            operation = method
            path = project_path(str(params.get("project") or ""))
            request = {
                "protocol": SERVICE_PROTOCOL,
                "version": SERVICE_PROTOCOL_VERSION,
                "operation": operation,
                "project": str(path),
                "arguments": (
                    {
                        "task_id": str(params.get("task_id") or ""),
                        **(
                            {"timeout": float(params.get("timeout", 3600))}
                            if method == "task.wait"
                            else {}
                        ),
                    }
                    if method != "task.list"
                    else {}
                ),
                "request_id": params.get("request_id"),
                "base_revision": params.get("base_revision"),
                "actor": params.get("actor")
                or {"kind": "system", "id": "editor-service", "name": "Editor Service"},
                "client_id": str(params.get("client_id") or "editor-service"),
            }
            return await asyncio.to_thread(sessions.execute, request)
        workspaces = self._workspaces
        if method.startswith("workspace.") and workspaces is None:
            raise RuntimeError("Workspace registry is not ready")
        if method == "workspace.attach":
            assert workspaces is not None
            return workspaces.attach(
                client_id=str(params.get("client_id") or ""),
                project=(str(params["project"]) if params.get("project") else None),
                workspace_session_id=(
                    str(params["workspace_session_id"])
                    if params.get("workspace_session_id")
                    else None
                ),
            )
        if method == "workspace.command":
            assert workspaces is not None
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError("workspace command arguments must be an object")
            event = workspaces.command(
                str(params.get("workspace_session_id") or ""),
                str(params.get("command") or ""),
                arguments,
            )
            events = self._event_hub
            if events is None:
                raise RuntimeError("Editor Service events are not ready")
            events.publish(ServiceEvent("workspace.changed", event))
            return event
        if method == "workspace.detach":
            assert workspaces is not None
            workspaces.detach(
                str(params.get("workspace_session_id") or ""),
                str(params.get("client_id") or ""),
            )
            return {"detached": True}
        if method == "service.shutdown":
            force = params.get("force", False)
            if not isinstance(force, bool):
                raise ValueError("force must be a boolean")
            result = await asyncio.to_thread(sessions.prepare_shutdown, force=force)
            asyncio.get_running_loop().call_soon(self.request_stop)
            return result
        raise ValueError(f"Unknown JSON-RPC method: {method}")

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        events = self._event_hub
        sessions = self._sessions
        if events is None or sessions is None:
            raise web.HTTPServiceUnavailable()
        websocket = web.WebSocketResponse(heartbeat=20)
        await websocket.prepare(request)
        subscription, queue = events.subscribe()
        workspace_subscription: tuple[str, str] | None = None
        subscribed_project_id = ""
        subscribed_project_path: Path | None = None
        subscribed_workspace_id = ""
        subscription_ready = asyncio.Event()

        async def send_events() -> None:
            while True:
                await subscription_ready.wait()
                event = await queue.get()
                payload = event.payload
                if event.type in {"project.changed", "project.conflict"}:
                    if str(payload.get("project_id") or "") != subscribed_project_id:
                        continue
                elif event.type == "task.changed":
                    event_project = str(payload.get("project_path") or "")
                    if (
                        subscribed_project_path is None
                        or not event_project
                        or Path(event_project).resolve() != subscribed_project_path
                    ):
                        continue
                elif event.type == "workspace.changed":
                    if (
                        not subscribed_workspace_id
                        or str(payload.get("workspace_session_id") or "")
                        != subscribed_workspace_id
                    ):
                        continue
                await websocket.send_json(event.as_dict())

        sender = asyncio.create_task(send_events())
        await websocket.send_json(
            ServiceEvent(
                "service.ready",
                {
                    "protocol": SERVICE_PROTOCOL,
                    "protocol_version": SERVICE_PROTOCOL_VERSION,
                },
            ).as_dict()
        )
        try:
            async for message in websocket:
                if message.type == WSMsgType.TEXT:
                    value = json.loads(message.data)
                    if not isinstance(value, dict):
                        raise ValueError("WebSocket messages must be objects")
                    if value.get("type") == "service.subscribe":
                        subscription_ready.clear()
                        await websocket.send_json(
                            ServiceEvent(
                                "service.subscribed",
                                {
                                    "protocol": SERVICE_PROTOCOL,
                                    "protocol_version": SERVICE_PROTOCOL_VERSION,
                                },
                            ).as_dict()
                        )
                        subscription_ready.set()
                        continue
                    if value.get("type") != "project.subscribe":
                        raise ValueError(
                            "WebSocket messages must be service.subscribe or "
                            "project.subscribe objects"
                        )
                    path = project_path(str(value.get("project") or ""))
                    project_cursor = int(value.get("project_cursor", 0))
                    task_cursor = int(value.get("task_cursor", 0))
                    subscription_ready.clear()
                    snapshot = await asyncio.to_thread(
                        sessions.project_subscription,
                        path,
                        project_cursor=project_cursor,
                        task_cursor=task_cursor,
                    )
                    subscribed_project_path = path.resolve()
                    subscribed_project_id = str(snapshot["project_id"])
                    for event in snapshot["project_events"]:
                        await websocket.send_json(
                            ServiceEvent("project.changed", event).as_dict()
                        )
                    for event in snapshot["task_events"]:
                        await websocket.send_json(
                            ServiceEvent("task.changed", event).as_dict()
                        )
                    workspace_session_id = str(
                        value.get("workspace_session_id") or ""
                    )
                    workspace_client_id = str(value.get("client_id") or "")
                    if workspace_session_id:
                        workspaces = self._workspaces
                        if workspaces is None:
                            raise RuntimeError("Workspace registry is not ready")
                        if workspace_subscription is not None:
                            workspaces.disconnect(*workspace_subscription)
                        workspaces.connect(
                            workspace_session_id,
                            workspace_client_id,
                        )
                        workspace_subscription = (
                            workspace_session_id,
                            workspace_client_id,
                        )
                    subscribed_workspace_id = workspace_session_id
                    await websocket.send_json(
                        ServiceEvent(
                            "project.subscribed",
                            {
                                "project": str(path),
                                "project_id": subscribed_project_id,
                                "project_cursor": snapshot["project_event_cursor"],
                                "task_cursor": snapshot["task_cursor"],
                                "workspace_session_id": workspace_session_id or None,
                            },
                        ).as_dict()
                    )
                    subscription_ready.set()
                elif message.type == WSMsgType.ERROR:
                    break
        finally:
            if workspace_subscription is not None and self._workspaces is not None:
                self._workspaces.disconnect(*workspace_subscription)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            events.unsubscribe(subscription)
        return websocket
