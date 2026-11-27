from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from mediaflow.application.timeline_snapping import snap_frame
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.timeline_history import TimelineStateChange

from .commands import DESKTOP_COMMANDS, desktop_command
from .remote_timeline_cache import apply_timeline_change

if TYPE_CHECKING:
    from mediaflow.application.timeline_editor import TimelineEditor as _TimelineCommandSurface

    from .remote_project import RemoteEditorProject
else:

    class _TimelineCommandSurface:
        pass


class _RemoteTimelineMethod:
    def __init__(self, command: str) -> None:
        self.definition = desktop_command("timeline", command)

    def __set_name__(self, owner: type[Any], name: str) -> None:
        if name != self.definition.name:
            raise RuntimeError(f"Remote timeline member {name} does not match {self.definition.name}")

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Any:
        if instance is None:
            return self

        def invoke(*args: Any, **kwargs: Any) -> Any:
            result, _revision = instance._project._call_revisioned(
                "timeline",
                self.definition.name,
                instance.sequence_id,
                *args,
                **kwargs,
            )
            return result

        return invoke


class RemoteTimelineEditor(_TimelineCommandSurface):
    snap_frame = staticmethod(snap_frame)

    def __init__(self, project: RemoteEditorProject, sequence_id: str):
        self._project = project
        self.sequence_id = sequence_id
        self._cache_lock = threading.RLock()
        self._cached_state: TimelineState | None = None
        self._cached_revision = -1
        self._cached_duration_frames: int | None = None

    @property
    def state(self):
        with self._cache_lock:
            if self._cached_state is not None and (
                self._cached_revision == self._project.known_content_revision
            ):
                return self._cached_state
        return self._read_state("state")

    def _read_state(self, command: str) -> TimelineState:
        for _attempt in range(3):
            value, revision = self._project._call_revisioned("timeline", command, self.sequence_id)
            if not isinstance(value, TimelineState):
                raise RuntimeError("Editor Service returned an invalid timeline state")
            with self._cache_lock:
                if revision == self._project.known_content_revision:
                    self._cached_state = value
                    self._cached_revision = revision
                    self._cached_duration_frames = value.duration_frames
                    return value
        self.invalidate()
        raise RuntimeError("项目持续变化，无法取得当前时间线，请重试")

    @property
    def duration_frames(self) -> int:
        state = self.state
        with self._cache_lock:
            if self._cached_state is state and self._cached_duration_frames is not None:
                return self._cached_duration_frames
        return state.duration_frames

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
        return self._read_state("reload")

    def invalidate(self) -> None:
        with self._cache_lock:
            self._cached_state = None
            self._cached_revision = -1
            self._cached_duration_frames = None

    def advance_revision(self, revision: int, *, base_revision: int) -> None:
        """Keep an exact cached timeline across a proven unrelated project write."""

        with self._cache_lock:
            if self._cached_state is not None:
                if self._cached_revision == base_revision and revision in {base_revision, base_revision + 1}:
                    self._cached_revision = int(revision)
                else:
                    self.invalidate()

    def apply_committed_change(
        self,
        change: TimelineStateChange | None,
        *,
        revision: int,
    ) -> None:
        with self._cache_lock:
            if self._cached_state is None:
                return
            # A reentrant UI read may have fetched the committed snapshot while
            # the write response was in flight. That exact revision is already
            # authoritative; applying its patch again would discard fresh data.
            if (
                change is not None
                and self._cached_revision == revision == self._project.known_content_revision
            ):
                return
            if (
                change is None
                or self._cached_revision != change.base_revision
                or revision not in {change.base_revision, change.base_revision + 1}
                or revision != self._project.known_content_revision
            ):
                self.invalidate()
                return
            self._cached_state = apply_timeline_change(self._cached_state, change)
            self._cached_revision = revision
            self._cached_duration_frames = self._cached_state.duration_frames


def _install_timeline_commands() -> None:
    for (target, name), _definition in DESKTOP_COMMANDS.items():
        if target != "timeline" or name in RemoteTimelineEditor.__dict__:
            continue
        descriptor = _RemoteTimelineMethod(name)
        descriptor.__set_name__(RemoteTimelineEditor, name)
        setattr(RemoteTimelineEditor, name, descriptor)


_install_timeline_commands()
