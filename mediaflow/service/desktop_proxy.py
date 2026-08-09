from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType

from mediaflow.application.events import TaskEvent
from mediaflow.application.timeline_snapping import snap_frame
from mediaflow.domain.collaboration import (
    ActorIdentity,
    ProjectChangeEvent,
    project_write_paths_overlap,
)
from mediaflow.domain.enums import ClipMediaKind
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.runtime import DesktopRuntimeDescriptor
from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.domain.timeline import Clip, TimelineState
from mediaflow.infrastructure.font_assets import subtitle_font_options
from mediaflow.infrastructure.settings_repository import DesktopSettingsRepository

from .client import (
    EditorServiceClient,
    EditorServiceRpcError,
    call_sync,
    close_sync_transport,
)
from .codec import decode_transport, encode_transport
from .commands import command_write_set, project_command, timeline_command

logger = logging.getLogger(__name__)

ProjectEventHandler = Callable[[ProjectChangeEvent], None]
TaskEventHandler = Callable[[TaskEvent], None]
WorkspaceEventHandler = Callable[[dict[str, Any]], None]

REVISION_CACHED_PROJECT_READS = frozenset(
    {
        "get_project",
        "get_sequence",
        "list_asset_bins",
        "list_assets",
        "list_audio_buses",
        "list_audio_effects",
        "list_export_history",
        "list_highlights",
        "list_sequences",
        "list_subtitle_documents",
        "list_subtitle_placements",
        "list_subtitle_segments",
        "list_subtitle_words",
        "list_versions",
        "selected_highlights",
        "selected_subtitle_segments_srt",
        "subtitle_segment_summary",
    }
)


@dataclass(frozen=True, slots=True)
class _PendingDesktopWrite:
    target: str
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
            and str(payload.get("workspace_session_id") or "")
            == self._workspace_session_id
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


class RemoteTimelineEditor:
    snap_frame = staticmethod(snap_frame)

    def __init__(self, project: RemoteEditorProject, sequence_id: str):
        self._project = project
        self.sequence_id = sequence_id
        self._cached_state: TimelineState | None = None
        self._cached_revision = -1

    @property
    def state(self):
        revision = self._project.known_content_revision
        if self._cached_state is None or self._cached_revision != revision:
            value = self._project._call("timeline", "state", self.sequence_id)
            if not isinstance(value, TimelineState):
                raise RuntimeError("Editor Service returned an invalid timeline state")
            self._cached_state = value
            self._cached_revision = self._project.known_content_revision
        return self._cached_state

    @property
    def can_undo(self) -> bool:
        return self._project.can_undo

    @property
    def can_redo(self) -> bool:
        return self._project.can_redo

    def undo(self) -> TimelineState:
        self._project.undo()
        return self.reload()

    def redo(self) -> TimelineState:
        self._project.redo()
        return self.reload()

    def reload(self) -> TimelineState:
        value = self._project._call("timeline", "reload", self.sequence_id)
        if not isinstance(value, TimelineState):
            raise RuntimeError("Editor Service returned an invalid reloaded timeline")
        self._cached_state = value
        self._cached_revision = self._project.known_content_revision
        return value

    def __getattr__(self, name: str):
        definition = timeline_command(name)

        def invoke(*args, **kwargs):
            result = self._project._call(
                "timeline",
                name,
                self.sequence_id,
                *args,
                **kwargs,
            )
            if definition.access == "write":
                self._apply_write(name, result)
            return result

        return invoke

    def invalidate(self) -> None:
        self._cached_state = None
        self._cached_revision = -1

    def _apply_write(self, command: str, result: Any) -> None:
        state = self._cached_state
        if state is None:
            return
        moved = result if isinstance(result, list) else [result]
        if (
            command not in {"move_clip", "move_clips"}
            or not moved
            or not all(isinstance(item, Clip) for item in moved)
            or state.transitions
            or any(item.media_kind == ClipMediaKind.LINKED_AV for item in moved)
        ):
            self.invalidate()
            return
        replacements = {item.id: item for item in moved}
        self._cached_state = state.model_copy(
            update={
                "clips": [
                    replacements.get(item.id, item)
                    for item in state.clips
                ]
            }
        )
        self._cached_revision = self._project.known_content_revision


class RemoteEditorProject:
    def __init__(
        self,
        descriptor: dict[str, Any],
        *,
        actor: ActorIdentity,
        workspace_session_id: str,
    ):
        self.project_dir = Path(str(descriptor["project"])).resolve()
        self.read_only = bool(descriptor.get("read_only", False))
        self._owns_project_writer = bool(descriptor.get("owns_project_writer", False))
        self._known_content_revision = int(descriptor.get("project_revision", 0))
        self._can_undo: bool | None = None
        self._can_redo: bool | None = None
        self._actor = actor
        self._events = _ProjectEventStream(
            self.project_dir,
            str(descriptor["project_id"]),
            self._known_content_revision,
            int(descriptor.get("project_event_cursor", 0)),
            workspace_session_id=workspace_session_id,
            client_id=actor.id,
        )
        self._timelines: dict[str, RemoteTimelineEditor] = {}
        self._drafts: dict[str, int] = {}
        self._draft_lock = threading.RLock()
        self._read_cache: dict[str, Any] = {}
        self._read_cache_revision = self.known_content_revision
        self._pending_write: _PendingDesktopWrite | None = None
        self._closed = False

    @property
    def known_content_revision(self) -> int:
        return max(self._known_content_revision, self._events.project_revision)

    @property
    def owns_project_writer(self) -> bool:
        return self._owns_project_writer

    @property
    def actor_id(self) -> str:
        return self._actor.id

    @property
    def actor_identity(self) -> ActorIdentity:
        return self._actor.model_copy(deep=True)

    @property
    def can_undo(self) -> bool:
        if self._can_undo is None:
            self._refresh_history_state()
        return bool(self._can_undo)

    @property
    def can_redo(self) -> bool:
        if self._can_redo is None:
            self._refresh_history_state()
        return bool(self._can_redo)

    def undo(self) -> None:
        self._execute_history("undo")

    def redo(self) -> None:
        self._execute_history("redo")

    def content_revision(self) -> int:
        value = int(self._call("project", "content_revision", ""))
        self._known_content_revision = max(self._known_content_revision, value)
        return value

    def _refresh_history_state(self) -> None:
        response = call_sync(
            "history.list",
            {"project": str(self.project_dir)},
        )
        if not isinstance(response, dict):
            raise RuntimeError("Editor Service returned an invalid history state")
        can_undo = response.get("can_undo")
        can_redo = response.get("can_redo")
        if not isinstance(can_undo, bool) or not isinstance(can_redo, bool):
            raise RuntimeError("Editor Service returned incomplete history state")
        self._known_content_revision = max(
            self._known_content_revision,
            int(response.get("project_revision", self._known_content_revision)),
        )
        self._can_undo = can_undo
        self._can_redo = can_redo

    def _execute_history(self, direction: str) -> None:
        base_revision = self.known_content_revision
        try:
            response = call_sync(
                f"history.{direction}",
                {
                    "project": str(self.project_dir),
                    "request_id": f"desktop-{uuid.uuid4().hex}",
                    "base_revision": base_revision,
                    "actor": self._actor.model_dump(mode="json"),
                },
            )
        except EditorServiceRpcError as error:
            if isinstance(error.data, dict) and "current_revision" in error.data:
                self._known_content_revision = int(error.data["current_revision"])
            raise
        if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
            raise RuntimeError("Editor Service returned an invalid history result")
        result = response["result"]
        can_undo = result.get("can_undo")
        can_redo = result.get("can_redo")
        if not isinstance(can_undo, bool) or not isinstance(can_redo, bool):
            raise RuntimeError("Editor Service omitted the updated history state")
        self._known_content_revision = max(
            self._known_content_revision,
            int(response.get("project_revision", self._known_content_revision)),
        )
        self._can_undo = can_undo
        self._can_redo = can_redo
        self._pending_write = None
        self._invalidate_read_cache()

    def timeline(self, sequence_id: str) -> RemoteTimelineEditor:
        editor = self._timelines.get(sequence_id)
        if editor is None:
            editor = RemoteTimelineEditor(self, sequence_id)
            self._timelines[sequence_id] = editor
        return editor

    def task_snapshot(self) -> tuple[list[Any], int]:
        value = self._call("project", "task_snapshot", "")
        if not isinstance(value, tuple) or len(value) != 2:
            raise RuntimeError("Editor Service returned an invalid task snapshot")
        tasks, cursor = value
        if not isinstance(tasks, list):
            raise RuntimeError("Editor Service returned invalid task snapshot items")
        self._events.task_cursor = int(cursor)
        return tasks, self._events.task_cursor

    def subscribe_project_events(
        self,
        handler: ProjectEventHandler,
        *,
        include_snapshot: bool = False,
    ) -> int:
        if include_snapshot:
            for event in self.list_project_events(after_cursor=0):
                handler(event)
        return self._events.subscribe_project(handler)

    def subscribe_task_events(
        self,
        handler: TaskEventHandler,
        *,
        include_snapshot: bool = True,
    ) -> int:
        if include_snapshot:
            for event in self.task_events_after(0):
                handler(event)
        return self._events.subscribe_task(handler)

    def subscribe_workspace_events(self, handler: WorkspaceEventHandler) -> int:
        return self._events.subscribe_workspace(handler)

    def unsubscribe_project_events(self, token: int) -> None:
        self._events.unsubscribe(token)

    def unsubscribe_task_events(self, token: int) -> None:
        self._events.unsubscribe(token)

    def unsubscribe_workspace_events(self, token: int) -> None:
        self._events.unsubscribe(token)

    def reload_external_changes(self) -> None:
        self._known_content_revision = self._events.project_revision
        self._can_undo = None
        self._can_redo = None
        self._invalidate_read_cache()

    def begin_draft(self, path: str) -> None:
        normalized = path.rstrip("/")
        if not normalized:
            return
        with self._draft_lock:
            self._drafts.setdefault(normalized, self.known_content_revision)

    def end_draft(self, path: str) -> None:
        with self._draft_lock:
            self._drafts.pop(path.rstrip("/"), None)

    def resolve_pending_conflict(self, resolution: str) -> Any:
        pending = self._pending_write
        if pending is None:
            return None
        if resolution not in {"keep_local", "accept_remote"}:
            raise ValueError(f"Unknown collaboration conflict resolution: {resolution}")
        self._pending_write = None
        with self._draft_lock:
            for path in tuple(self._drafts):
                if any(project_write_paths_overlap(path, changed) for changed in pending.write_set):
                    self._drafts.pop(path, None)
        if resolution == "accept_remote":
            self.reload_external_changes()
            return None
        return self._call(
            pending.target,
            pending.command,
            pending.sequence_id,
            *pending.args,
            **pending.kwargs,
        )

    def close(self, *, timeout: float | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        self._events.close()
        call_sync(
            "project.close",
            {
                "project": str(self.project_dir),
                "client_id": self._actor.id,
                "timeout_seconds": 5.0 if timeout is None else float(timeout),
            },
        )

    def __getattr__(self, name: str):
        project_command(name)
        return lambda *args, **kwargs: self._call(
            "project",
            name,
            "",
            *args,
            **kwargs,
        )

    def _call(
        self,
        target: str,
        command: str,
        timeline_sequence_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        definition = (
            project_command(command)
            if target == "project"
            else timeline_command(command)
        )
        cache_key = ""
        if target == "project" and command in REVISION_CACHED_PROJECT_READS:
            self._ensure_read_cache_revision()
            cache_key = json.dumps(
                [command, timeline_sequence_id, encode_transport(list(args)), encode_transport(kwargs)],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if cache_key in self._read_cache:
                return self._read_cache[cache_key]
        write_set: list[str] = []
        base_revision = self.known_content_revision
        previous_revision = base_revision
        if definition.access == "write":
            write_set = command_write_set(
                target=target,
                command=command,
                sequence_id=timeline_sequence_id,
                args=list(args),
                kwargs=kwargs,
            )
            with self._draft_lock:
                draft_revisions = [
                    revision
                    for path, revision in self._drafts.items()
                    if any(project_write_paths_overlap(path, changed) for changed in write_set)
                ]
            if draft_revisions:
                base_revision = min(draft_revisions)
        request_id = f"desktop-{uuid.uuid4().hex}"
        try:
            response = call_sync(
                "desktop.project.call",
                {
                    "project": str(self.project_dir),
                    "target": target,
                    "sequence_id": timeline_sequence_id,
                    "command": command,
                    "args": encode_transport(list(args)),
                    "kwargs": encode_transport(kwargs),
                    "base_revision": base_revision,
                    "request_id": request_id,
                    "actor": self._actor.model_dump(mode="json"),
                },
            )
        except EditorServiceRpcError as error:
            if isinstance(error.data, dict) and "current_revision" in error.data:
                self._known_content_revision = int(error.data["current_revision"])
            if error.code == -32009 and definition.access == "write":
                self._pending_write = _PendingDesktopWrite(
                    target=target,
                    command=command,
                    sequence_id=timeline_sequence_id,
                    args=tuple(args),
                    kwargs=dict(kwargs),
                    write_set=tuple(write_set),
                )
            raise
        if not isinstance(response, dict):
            raise RuntimeError("Editor Service returned an invalid desktop command response")
        self._known_content_revision = max(
            self._known_content_revision,
            int(response.get("project_revision", self._known_content_revision)),
        )
        result = decode_transport(response.get("value"))
        if definition.access == "write":
            history = response.get("history")
            if (
                not isinstance(history, dict)
                or not isinstance(history.get("can_undo"), bool)
                or not isinstance(history.get("can_redo"), bool)
            ):
                raise RuntimeError(
                    "Editor Service omitted the history state for a desktop write"
                )
            self._can_undo = history["can_undo"]
            self._can_redo = history["can_redo"]
        if definition.access == "write" and self.known_content_revision != previous_revision:
            self._invalidate_read_cache(invalidate_timelines=target != "timeline")
        elif cache_key:
            self._read_cache[cache_key] = result
        return result

    def _ensure_read_cache_revision(self) -> None:
        if self._read_cache_revision != self.known_content_revision:
            self._invalidate_read_cache()

    def _invalidate_read_cache(self, *, invalidate_timelines: bool = True) -> None:
        self._read_cache.clear()
        self._read_cache_revision = self.known_content_revision
        if invalidate_timelines:
            for editor in self._timelines.values():
                editor.invalidate()


class DesktopEditorApplication:
    """Desktop-local rendering facilities with service-owned persistent projects."""

    def __init__(self):
        self._actor = ActorIdentity(
            kind="human",
            id=f"desktop-{uuid.uuid4().hex}",
            name="MediaFlow Pro desktop",
        )
        descriptor_value = call_sync("system.runtime.inspect")
        if not isinstance(descriptor_value, dict):
            raise RuntimeError("Editor Service returned an invalid runtime descriptor")
        self.runtime_descriptor = DesktopRuntimeDescriptor.model_validate(
            descriptor_value
        )
        workspace = call_sync(
            "workspace.attach",
            {"client_id": self._actor.id},
        )
        if not isinstance(workspace, dict) or not workspace.get("workspace_session_id"):
            raise RuntimeError("Editor Service returned an invalid workspace session")
        self.workspace_session_id = str(workspace["workspace_session_id"])
        settings = decode_transport(call_sync("desktop.application.settings"))
        if not isinstance(settings, ServiceSettings):
            raise RuntimeError("Editor Service returned invalid application settings")
        self._service_settings = settings
        self._desktop_settings_repository = DesktopSettingsRepository()
        self._desktop_settings = self._desktop_settings_repository.load()
        self.cookies = _RemoteCookieStore()

    @property
    def service_settings(self) -> ServiceSettings:
        return self._service_settings

    @property
    def desktop_settings(self) -> DesktopSettings:
        return self._desktop_settings

    @property
    def native_qml_root(self) -> Path | None:
        path = Path(self.runtime_descriptor.native_qml)
        return path if path.is_dir() else None

    @property
    def mlt_runtime_root(self) -> str:
        return self.runtime_descriptor.mlt_root

    @property
    def mlt_library_path(self) -> str:
        return self.runtime_descriptor.mlt_library

    @property
    def mlt_repository_path(self) -> str:
        return self.runtime_descriptor.mlt_repository

    @property
    def mlt_preview_repository_path(self) -> str:
        return self.runtime_descriptor.mlt_preview_repository

    @property
    def mlt_data_path(self) -> str:
        return self.runtime_descriptor.mlt_data

    def replace_service_settings(self, settings: ServiceSettings) -> None:
        value = call_sync(
            "desktop.application.settings.replace",
            {"settings": encode_transport(settings)},
        )
        accepted = decode_transport(value)
        if not isinstance(accepted, ServiceSettings):
            raise RuntimeError("Editor Service returned invalid application settings")
        self._service_settings = accepted

    def replace_desktop_settings(self, settings: DesktopSettings) -> None:
        self._desktop_settings_repository.save(settings)
        self._desktop_settings = settings.model_copy(deep=True)

    def close_client_transport(self) -> None:
        try:
            call_sync(
                "workspace.detach",
                {
                    "workspace_session_id": self.workspace_session_id,
                    "client_id": self._actor.id,
                },
                start_if_needed=False,
            )
        except (EditorServiceRpcError, RuntimeError):
            logger.exception("Failed to detach the desktop workspace session")
        finally:
            close_sync_transport()

    def discover_encoder_policy_options(self) -> list[dict]:
        return self._application_call("discover_encoder_policy_options")

    @property
    def default_media_directory(self) -> str:
        return self._application_call("default_media_directory")

    def subtitle_font_options(self) -> list[dict]:
        # Font availability is a GUI-process capability. The resident service
        # must stay headless and never initialize QtGui just to inspect fonts.
        return subtitle_font_options()

    def run_runtime_tool(
        self,
        operation: str,
        arguments: dict | None = None,
        *,
        progress: Callable[[Any], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Any:
        if check_cancelled is not None:
            check_cancelled()
        result = asyncio.run(
            self._run_runtime_tool_with_events(
                operation,
                arguments or {},
                progress=progress,
            )
        )
        if check_cancelled is not None:
            check_cancelled()
        return result

    @staticmethod
    async def _run_runtime_tool_with_events(
        operation: str,
        arguments: dict[str, Any],
        *,
        progress: Callable[[Any], None] | None,
    ) -> Any:
        from mediaflow.domain.progress import OperationProgress

        client = await EditorServiceClient.connect()
        timeout = ClientTimeout(total=None, connect=5, sock_read=None)
        async with ClientSession(timeout=timeout) as session:
            async with session.ws_connect(
                client.discovery.websocket_url,
                headers={
                    "Authorization": f"Bearer {client.discovery.token}",
                },
                heartbeat=20,
            ) as websocket:
                ready = await websocket.receive_json()
                if not isinstance(ready, dict) or ready.get("type") != "service.ready":
                    raise RuntimeError("Editor Service event stream did not become ready")
                await websocket.send_json({"type": "service.subscribe"})
                subscribed = await websocket.receive_json()
                if (
                    not isinstance(subscribed, dict)
                    or subscribed.get("type") != "service.subscribed"
                ):
                    raise RuntimeError("Editor Service event subscription was not acknowledged")

                async def consume_progress() -> None:
                    async for message in websocket:
                        if message.type != WSMsgType.TEXT:
                            if message.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                                return
                            continue
                        value = json.loads(message.data)
                        if not isinstance(value, dict) or value.get("type") != "runtime.changed":
                            continue
                        payload = value.get("payload")
                        if (
                            not isinstance(payload, dict)
                            or payload.get("operation") != operation
                            or not isinstance(payload.get("progress"), dict)
                            or progress is None
                        ):
                            continue
                        progress(OperationProgress.model_validate(payload["progress"]))

                consumer = asyncio.create_task(consume_progress())
                try:
                    value = await client.call(
                        "desktop.application.call",
                        {
                            "command": "run_runtime_tool",
                            "args": encode_transport([operation]),
                            "kwargs": encode_transport({"arguments": arguments}),
                        },
                        session=session,
                    )
                    return decode_transport(value)
                finally:
                    consumer.cancel()
                    await asyncio.gather(consumer, return_exceptions=True)

    def cancel_runtime_tool(self) -> dict[str, Any]:
        value = self._application_call("cancel_runtime_tool")
        if not isinstance(value, dict):
            raise RuntimeError("Editor Service returned an invalid runtime cancellation result")
        return value

    def analyze_download_url(
        self,
        url: str,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ):
        if check_cancelled is not None:
            check_cancelled()
        return self._application_call("analyze_download_url", url)

    def test_llm_provider(self, provider) -> None:
        self._application_call("test_llm_provider", provider)

    def runtime_tool_status(self) -> dict:
        return self._application_call("runtime_tool_status")

    def installed_asr_models(self) -> frozenset[str]:
        return self._application_call("installed_asr_models")

    def recent_projects(self, paths: list[str]):
        return self._application_call("recent_projects", paths)

    def asset_thumbnail_paths(self, project_dir: str | Path, **kwargs: Any):
        return self._application_call("asset_thumbnail_paths", project_dir, **kwargs)

    def timeline_filmstrip_paths(
        self,
        project_dir: str | Path,
        sequence_id: str,
        **kwargs: Any,
    ):
        return self._application_call(
            "timeline_filmstrip_paths",
            project_dir,
            sequence_id,
            **kwargs,
        )

    def cancel_timeline_filmstrip_requests(
        self,
        project_dir: str | Path,
        **kwargs: Any,
    ) -> None:
        self._application_call(
            "cancel_timeline_filmstrip_requests",
            project_dir,
            **kwargs,
        )

    def write_preview_snapshot(self, project_dir: str | Path, state, **kwargs: Any):
        return self._application_call("write_preview_snapshot", project_dir, state, **kwargs)

    def write_asset_preview_snapshot(
        self,
        project_dir: str | Path,
        sequence_id: str,
        asset_id: str,
    ):
        return self._application_call(
            "write_asset_preview_snapshot",
            project_dir,
            sequence_id,
            asset_id,
        )

    @staticmethod
    def _application_call(command: str, *args: Any, **kwargs: Any) -> Any:
        value = call_sync(
            "desktop.application.call",
            {
                "command": command,
                "args": encode_transport(list(args)),
                "kwargs": encode_transport(kwargs),
            },
        )
        return decode_transport(value)

    def create_project(
        self,
        root: str | Path,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> RemoteEditorProject:
        path = Path(root).expanduser().resolve()
        profile_confirmed = profile is not None
        descriptor = call_sync(
            "project.create",
            {
                "project": str(path),
                "name": name,
                "profile": encode_transport(profile or ProjectProfile()),
                "profile_confirmed": profile_confirmed,
                "client_id": self._actor.id,
            },
        )
        if not isinstance(descriptor, dict):
            raise RuntimeError("Editor Service returned an invalid project descriptor")
        self._attach_workspace(path)
        return RemoteEditorProject(
            descriptor,
            actor=self._actor,
            workspace_session_id=self.workspace_session_id,
        )

    def open_project(
        self,
        root: str | Path,
        *,
        writable: bool = True,
    ) -> RemoteEditorProject:
        if not writable:
            raise ValueError("Desktop service sessions are writable single-writer sessions")
        descriptor = call_sync(
            "project.open",
            {
                "project": str(Path(root).expanduser().resolve()),
                "client_id": self._actor.id,
            },
        )
        if not isinstance(descriptor, dict):
            raise RuntimeError("Editor Service returned an invalid project descriptor")
        self._attach_workspace(Path(str(descriptor["project"])))
        return RemoteEditorProject(
            descriptor,
            actor=self._actor,
            workspace_session_id=self.workspace_session_id,
        )

    def _attach_workspace(self, project: Path) -> None:
        call_sync(
            "workspace.attach",
            {
                "workspace_session_id": self.workspace_session_id,
                "client_id": self._actor.id,
                "project": str(project.resolve()),
            },
        )

def create_desktop_editor_application() -> DesktopEditorApplication:
    started_at = time.monotonic()
    application = DesktopEditorApplication()
    logger.info("Desktop Editor Service bridge ready in %.3fs", time.monotonic() - started_at)
    return application


class _RemoteCookieStore:
    def status(self, *args: Any) -> Any:
        return self._call("status", *args)

    def save(self, *args: Any) -> Any:
        return self._call("save", *args)

    def clear(self, *args: Any) -> Any:
        return self._call("clear", *args)

    @staticmethod
    def _call(command: str, *args: Any) -> Any:
        value = call_sync(
            "desktop.application.cookies",
            {
                "command": command,
                "args": encode_transport(list(args)),
            },
        )
        return decode_transport(value)
