from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType

from mediaflow.application.events import TaskEvent
from mediaflow.domain.collaboration import ProjectChangeEvent

from .client import EditorServiceClient
from .commands import DesktopTarget

logger = logging.getLogger(__name__)

ProjectEventHandler = Callable[[ProjectChangeEvent], None]
TaskEventHandler = Callable[[TaskEvent], None]
WorkspaceEventHandler = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class _PendingDesktopWrite:
    target: DesktopTarget
    command: str
    sequence_id: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    write_set: tuple[str, ...]


class _ProjectEventStream:
    _SUBSCRIPTION_TIMEOUT_SECONDS = 15.0

    def __init__(
        self,
        project_dir: Path,
        project_id: str,
        revision: int,
        project_cursor: int,
        *,
        workspace_session_id: str,
        client_id: str,
    ):
        self.project_dir = project_dir.resolve()
        self.project_id = project_id
        self.project_revision = revision
        self.project_cursor = project_cursor
        self.task_cursor = 0
        self._project_handlers: dict[int, ProjectEventHandler] = {}
        self._task_handlers: dict[int, TaskEventHandler] = {}
        self._workspace_handlers: dict[int, WorkspaceEventHandler] = {}
        self._workspace_session_id = workspace_session_id
        self._client_id = client_id
        self._next_token = 1
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._consume_task: asyncio.Task[None] | None = None

    def subscribe_project(self, handler: ProjectEventHandler) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._project_handlers[token] = handler
        self._ensure_ready()
        return token

    def subscribe_task(self, handler: TaskEventHandler) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._task_handlers[token] = handler
        self._ensure_ready()
        return token

    def subscribe_workspace(self, handler: WorkspaceEventHandler) -> int:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._workspace_handlers[token] = handler
        self._ensure_ready()
        return token

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._project_handlers.pop(token, None)
            self._task_handlers.pop(token, None)
            self._workspace_handlers.pop(token, None)

    def close(self) -> None:
        self._stop.set()
        self._ready.set()
        loop = self._loop
        task = self._consume_task
        if loop is not None and task is not None and not task.done():
            loop.call_soon_threadsafe(task.cancel)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name=f"mediaflow-events-{self.project_dir.name}",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(self._SUBSCRIPTION_TIMEOUT_SECONDS):
            raise TimeoutError(
                "Editor Service project event subscription was not acknowledged "
                f"within {self._SUBSCRIPTION_TIMEOUT_SECONDS:g} seconds"
            )
        if self._stop.is_set():
            raise RuntimeError("Editor Service project event stream is closed")

    def _run(self) -> None:
        try:
            asyncio.run(self._consume_forever())
        except asyncio.CancelledError:
            if not self._stop.is_set():
                raise
        finally:
            self._consume_task = None
            self._loop = None

    async def _consume_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._consume_task = asyncio.current_task()
        delay = 0.1
        while not self._stop.is_set():
            try:
                client = await EditorServiceClient.connect()
                timeout = ClientTimeout(total=None, connect=5, sock_read=None)
                async with ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(
                        f"{client.discovery.base_url}/events",
                        headers={
                            "Authorization": f"Bearer {client.discovery.token}",
                        },
                        heartbeat=20,
                        max_msg_size=0,
                    ) as websocket:
                        self._ready.clear()
                        await websocket.send_json(
                            {
                                "type": "project.subscribe",
                                "project": str(self.project_dir),
                                "project_cursor": self.project_cursor,
                                "task_cursor": self.task_cursor,
                                "workspace_session_id": self._workspace_session_id,
                                "client_id": self._client_id,
                            }
                        )
                        delay = 0.1
                        while not self._stop.is_set():
                            try:
                                message = await asyncio.wait_for(
                                    websocket.receive(),
                                    timeout=0.25,
                                )
                            except TimeoutError:
                                continue
                            if message.type == WSMsgType.TEXT:
                                self._accept(json.loads(message.data))
                            elif message.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                                break
            except Exception:
                if not self._stop.is_set():
                    logger.exception("Editor Service event stream disconnected")
            self._ready.clear()
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, 5.0)

    def _accept(self, value: Any) -> None:
        if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
            return
        event_type = str(value.get("type") or "")
        payload = value["payload"]
        event_project = str(payload.get("project_path") or payload.get("project") or "")
        if event_project and Path(event_project).resolve() != self.project_dir:
            return
        event_project_id = str(payload.get("project_id") or "")
        if event_project_id and event_project_id != self.project_id:
            return
        if event_type == "project.subscribed":
            self.project_cursor = max(
                self.project_cursor,
                int(payload.get("project_cursor", self.project_cursor)),
            )
            self.task_cursor = max(
                self.task_cursor,
                int(payload.get("task_cursor", self.task_cursor)),
            )
            self._ready.set()
        elif event_type == "project.changed":
            cursor = int(payload.get("cursor", 0))
            if cursor <= self.project_cursor:
                return
            project_event = ProjectChangeEvent.model_validate(payload)
            self.project_cursor = project_event.cursor
            self.project_revision = project_event.project_revision
            with self._lock:
                project_handlers = tuple(self._project_handlers.values())
            self._deliver(project_handlers, project_event)
        elif event_type == "task.changed":
            cursor = int(payload.get("cursor", 0))
            if cursor <= self.task_cursor:
                return
            task_event = TaskEvent(
                task_id=str(payload.get("task_id") or ""),
                project_id=str(payload.get("project_id") or ""),
                event_type=str(payload.get("event_type") or ""),
                revision=int(payload.get("task_revision", 0)),
                payload=dict(payload.get("payload") or {}),
                cursor=cursor,
            )
            self.task_cursor = task_event.cursor
            with self._lock:
                task_handlers = tuple(self._task_handlers.values())
            self._deliver(task_handlers, task_event)
        elif (
            event_type == "workspace.changed"
            and str(payload.get("workspace_session_id") or "") == self._workspace_session_id
        ):
            with self._lock:
                workspace_handlers = tuple(self._workspace_handlers.values())
            self._deliver(workspace_handlers, dict(payload))

    @staticmethod
    def _deliver(handlers: tuple[Callable[[Any], None], ...], event: Any) -> None:
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("Desktop service event observer failed")
