from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaflow.domain.collaboration import (
    ActorIdentity,
    project_write_paths_overlap,
)

from .client import EditorServiceRpcError, call_sync
from .codec import decode_transport, encode_transport
from .commands import DESKTOP_COMMANDS, DesktopTarget, desktop_command
from .desktop_event_stream import (
    ProjectEventHandler,
    TaskEventHandler,
    WorkspaceEventHandler,
    _PendingDesktopWrite,
    _ProjectEventStream,
)
from .remote_timeline import RemoteTimelineEditor

if TYPE_CHECKING:
    from mediaflow.editor_project_delivery_commands import EditorProjectDeliveryCommands
    from mediaflow.editor_project_document_commands import EditorProjectDocumentCommands
    from mediaflow.editor_project_media_commands import EditorProjectMediaCommands
    from mediaflow.editor_project_task_commands import EditorProjectTaskWorkflowCommands
    from mediaflow.editor_project_web_commands import EditorProjectWebCommands

    class _ProjectCommandSurface(
        EditorProjectDocumentCommands,
        EditorProjectMediaCommands,
        EditorProjectTaskWorkflowCommands,
        EditorProjectWebCommands,
        EditorProjectDeliveryCommands,
    ):
        pass
else:

    class _ProjectCommandSurface:
        pass


REVISION_CACHED_PROJECT_READS = frozenset(
    {
        "get_project",
        "get_dubbing_session",
        "get_sequence",
        "list_asset_bins",
        "list_assets",
        "list_audio_buses",
        "list_audio_effects",
        "list_dubbing_sessions",
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


class _RemoteProjectMethod:
    def __init__(self, command: str) -> None:
        self.definition = desktop_command("project", command)

    def __set_name__(self, owner: type[Any], name: str) -> None:
        if name != self.definition.name:
            raise RuntimeError(f"Remote project member {name} does not match {self.definition.name}")

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Any:
        if instance is None:
            return self

        def invoke(*args: Any, **kwargs: Any) -> Any:
            return instance._call("project", self.definition.name, "", *args, **kwargs)

        return invoke


class RemoteEditorProject(_ProjectCommandSurface):
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
        self._remote_timelines: dict[str, RemoteTimelineEditor] = {}
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
            {
                "project": str(self.project_dir),
                "include_items": False,
            },
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
        editor = self._remote_timelines.get(sequence_id)
        if editor is None:
            editor = RemoteTimelineEditor(self, sequence_id)
            self._remote_timelines[sequence_id] = editor
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

    def _call(
        self,
        target: DesktopTarget,
        command: str,
        timeline_sequence_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        definition = desktop_command(target, command)
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
            write_set = definition.mutation_plan(
                sequence_id=timeline_sequence_id,
                args=list(args),
                kwargs=kwargs,
            ).conflict_set
            with self._draft_lock:
                draft_revisions = [
                    revision
                    for path, revision in self._drafts.items()
                    if any(project_write_paths_overlap(path, changed) for changed in write_set)
                ]
            if draft_revisions:
                base_revision = min(draft_revisions)
        request_id = f"desktop-{uuid.uuid4().hex}"
        request = definition.validate_request(
            encode_transport(list(args)),
            encode_transport(kwargs),
        )
        try:
            response = call_sync(
                "desktop.project.call",
                {
                    "project": str(self.project_dir),
                    "target": target,
                    "sequence_id": timeline_sequence_id,
                    "command": command,
                    "args": request.args,
                    "kwargs": request.kwargs,
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
        event_ack = response.get("event_ack")
        if definition.access == "write" and isinstance(event_ack, dict):
            cursor = event_ack.get("cursor")
            project_revision = event_ack.get("project_revision")
            if isinstance(cursor, int) and isinstance(project_revision, int):
                self._events.acknowledge_project_event(
                    cursor=cursor,
                    project_revision=project_revision,
                )
        self._known_content_revision = max(
            self._known_content_revision,
            int(response.get("project_revision", self._known_content_revision)),
        )
        result = decode_transport(definition.validate_result(response.get("value")))
        if definition.access == "write":
            history = response.get("history")
            if (
                not isinstance(history, dict)
                or not isinstance(history.get("can_undo"), bool)
                or not isinstance(history.get("can_redo"), bool)
            ):
                raise RuntimeError("Editor Service omitted the history state for a desktop write")
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
            for editor in self._remote_timelines.values():
                editor.invalidate()


def _install_project_commands() -> None:
    for (target, name), _definition in DESKTOP_COMMANDS.items():
        if target != "project" or name in RemoteEditorProject.__dict__:
            continue
        descriptor = _RemoteProjectMethod(name)
        descriptor.__set_name__(RemoteEditorProject, name)
        setattr(RemoteEditorProject, name, descriptor)


_install_project_commands()
