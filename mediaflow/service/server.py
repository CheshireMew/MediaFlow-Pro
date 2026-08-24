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
from aiohttp import WSCloseCode, WSMsgType, web
from pydantic import ValidationError

from mediaflow.domain.collaboration import ProjectRevisionConflict
from mediaflow.infrastructure.project_lock import ProcessFileLock

from .deferred_application import DeferredEditorApplication
from .discovery import (
    SERVICE_PROTOCOL,
    SERVICE_PROTOCOL_VERSION,
    ServiceDiscovery,
    ServicePaths,
)
from .events import EVENT_STREAM_OVERFLOW, EventHub, ServiceEvent
from .execution import ServiceBusyError, ServiceExecutionPools
from .project_paths import project_path
from .request_dispatcher import ServiceRequestDispatcher
from .sessions import EditorServiceOperations
from .workspaces import WorkspaceRegistry

JSON_RPC_VERSION = "2.0"
PRIVATE_PORT_START = 49_152
SERVICE_SHUTDOWN_TIMEOUT_SECONDS = 2.0
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
        port = PRIVATE_PORT_START + secrets.randbelow(PRIVATE_PORT_END - PRIVATE_PORT_START + 1)
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen(socket.SOMAXCONN)
            listener.setblocking(False)
            return listener
        except OSError as error:
            last_error = error
            listener.close()
    raise RuntimeError("MediaFlow Editor Service could not reserve a private loopback port") from last_error


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
        application_factory: Callable[[], Any] = DeferredEditorApplication,
    ):
        self.paths = paths or ServicePaths.discover()
        self._application_factory = application_factory
        self._instance_lock = ProcessFileLock(self.paths.lock)
        self._runner: web.AppRunner | None = None
        self._site: web.SockSite | None = None
        self._socket: socket.socket | None = None
        self._stop = asyncio.Event()
        self._event_hub: EventHub | None = None
        self._operations: EditorServiceOperations | None = None
        self._execution: ServiceExecutionPools | None = None
        self._dispatcher: ServiceRequestDispatcher | None = None
        self._workspaces: WorkspaceRegistry | None = None
        self._websockets: set[web.WebSocketResponse] = set()
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
            self._operations = EditorServiceOperations(application, self._event_hub)
            self._execution = ServiceExecutionPools()
            self._dispatcher = ServiceRequestDispatcher(
                self._operations,
                self._workspaces,
                self._event_hub,
                self.request_stop,
                self._execution,
            )
            app = web.Application(
                middlewares=[self._authenticate],
                client_max_size=8 * 1024 * 1024,
            )
            app.router.add_get("/health", self._health)
            app.router.add_post("/rpc", self._rpc)
            app.router.add_get("/events", self._websocket)
            app.on_shutdown.append(self._shutdown_websockets)
            self._runner = web.AppRunner(
                app,
                access_log=None,
                shutdown_timeout=SERVICE_SHUTDOWN_TIMEOUT_SECONDS,
            )
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
            if self._operations is not None:
                try:
                    execution = self._execution
                    if execution is None:
                        self._operations.close()
                    else:
                        await execution.run("lifecycle", self._operations.close)
                except BaseException as error:
                    stop_error = error
                finally:
                    self._operations = None
            if self._workspaces is not None:
                self._workspaces.close()
                self._workspaces = None
            try:
                from mediaflow.infrastructure.web_capture_engine import (
                    shutdown_web_capture_engines,
                )

                execution = self._execution
                if execution is None:
                    shutdown_web_capture_engines()
                else:
                    await execution.run("lifecycle", shutdown_web_capture_engines)
            except BaseException as error:
                if stop_error is None:
                    stop_error = error
        finally:
            self._dispatcher = None
            if self._execution is not None:
                self._execution.close()
                self._execution = None
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

    async def _shutdown_websockets(self, _app: web.Application) -> None:
        websockets = tuple(self._websockets)
        if not websockets:
            return
        await asyncio.gather(
            *(
                websocket.close(
                    code=WSCloseCode.GOING_AWAY,
                    message=b"service stopping",
                )
                for websocket in websockets
            ),
            return_exceptions=True,
        )
        self._websockets.clear()

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
            response = _json_rpc_error(identifier, -32602, str(error), {"type": type(error).__name__})
        except FileNotFoundError as error:
            response = _json_rpc_error(identifier, -32004, str(error), {"type": type(error).__name__})
        except PermissionError as error:
            response = _json_rpc_error(identifier, -32003, str(error), {"type": type(error).__name__})
        except ProjectRevisionConflict as error:
            try:
                await self._publish_project_conflict(method, params, error)
            except Exception:
                logger.exception("Failed to publish the project conflict event")
            response = _json_rpc_error(identifier, -32009, str(error), error.as_dict())
        except ServiceBusyError as error:
            response = _json_rpc_error(
                identifier,
                -32029,
                str(error),
                {"type": type(error).__name__, "retryable": True},
            )
        except RuntimeError as error:
            response = _json_rpc_error(identifier, -32000, str(error), {"type": type(error).__name__})
        except Exception as error:
            response = _json_rpc_error(identifier, -32603, str(error), {"type": type(error).__name__})
        return web.json_response(response)

    async def _publish_project_conflict(
        self,
        method: str,
        params: dict[str, Any],
        error: ProjectRevisionConflict,
    ) -> None:
        operations = self._operations
        events = self._event_hub
        if operations is None or events is None:
            return
        context = self._conflict_request_context(method, params)
        project_value = str(context.get("project") or "")
        if not project_value:
            return
        execution = self._execution
        if execution is None:
            raise RuntimeError("Editor Service execution pools are not ready")
        identity = await execution.run(
            "project",
            operations.desktop.project_identity,
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
                if isinstance(raw_requests, list) and raw_requests and isinstance(raw_requests[0], dict)
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
        dispatcher = self._dispatcher
        if dispatcher is None:
            raise RuntimeError("Editor Service is not ready")
        return await dispatcher.dispatch(method, params)

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        events = self._event_hub
        operations = self._operations
        if events is None or operations is None:
            raise web.HTTPServiceUnavailable()
        websocket = web.WebSocketResponse(
            heartbeat=20,
            timeout=SERVICE_SHUTDOWN_TIMEOUT_SECONDS,
        )
        await websocket.prepare(request)
        self._websockets.add(websocket)
        scoped_event_types = frozenset(
            {"project.changed", "project.conflict", "task.changed", "workspace.changed"}
        )
        subscription, queue = events.subscribe(
            selector=lambda event: event.type not in scoped_event_types,
        )
        workspace_subscription: tuple[str, str] | None = None
        subscription_ready = asyncio.Event()

        async def send_events() -> None:
            while True:
                await subscription_ready.wait()
                event = await queue.get()
                await websocket.send_json(event.as_dict())
                if event.type in {"service.stopping", EVENT_STREAM_OVERFLOW}:
                    await websocket.close(
                        code=(
                            WSCloseCode.GOING_AWAY
                            if event.type == "service.stopping"
                            else WSCloseCode.TRY_AGAIN_LATER
                        ),
                        message=(
                            b"service stopping"
                            if event.type == "service.stopping"
                            else b"event replay required"
                        ),
                    )
                    return

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
                        events.replace_selector(
                            subscription,
                            lambda event: event.type not in scoped_event_types,
                        )
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
                            "WebSocket messages must be service.subscribe or project.subscribe objects"
                        )
                    path = project_path(str(value.get("project") or ""))
                    project_cursor = int(value.get("project_cursor", 0))
                    task_cursor = int(value.get("task_cursor", 0))
                    subscription_ready.clear()
                    workspace_session_id = str(value.get("workspace_session_id") or "")
                    project_client_id = str(value.get("client_id") or "")
                    execution = self._execution
                    if execution is None:
                        raise RuntimeError("Editor Service execution pools are not ready")
                    identity = await execution.run(
                        "project",
                        operations.desktop.project_identity,
                        path,
                    )
                    selected_project_id = str(identity["project_id"])
                    selected_project_path = path.resolve()

                    def selects_current_scope(
                        event: ServiceEvent,
                        *,
                        project_id: str = selected_project_id,
                        project_path_value: Path = selected_project_path,
                        workspace_id: str = workspace_session_id,
                        client_id: str = project_client_id,
                    ) -> bool:
                        if event.type in {"project.changed", "project.conflict"}:
                            if str(event.payload.get("project_id") or "") != project_id:
                                return False
                            if event.type == "project.changed" and client_id:
                                actor = event.payload.get("actor")
                                if isinstance(actor, dict) and str(actor.get("id") or "") == client_id:
                                    return False
                            return True
                        if event.type == "task.changed":
                            event_project = str(event.payload.get("project_path") or "")
                            return (
                                bool(event_project)
                                and Path(event_project).resolve() == project_path_value
                            )
                        if event.type == "workspace.changed":
                            return bool(workspace_id) and (
                                str(event.payload.get("workspace_session_id") or "")
                                == workspace_id
                            )
                        return True

                    events.replace_selector(subscription, selects_current_scope)
                    snapshot = await execution.run(
                        "project",
                        operations.desktop.project_subscription,
                        path,
                        project_cursor=project_cursor,
                        task_cursor=task_cursor,
                    )
                    for event in snapshot["project_events"]:
                        await websocket.send_json(ServiceEvent("project.changed", event).as_dict())
                    for event in snapshot["task_events"]:
                        await websocket.send_json(ServiceEvent("task.changed", event).as_dict())
                    workspace_client_id = project_client_id
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
                    await websocket.send_json(
                        ServiceEvent(
                            "project.subscribed",
                            {
                                "project": str(path),
                                "project_id": str(snapshot["project_id"]),
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
            self._websockets.discard(websocket)
        return websocket
