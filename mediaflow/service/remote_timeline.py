from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mediaflow.application.timeline_snapping import snap_frame
from mediaflow.domain.timeline import TimelineState

from .commands import DESKTOP_COMMANDS, desktop_command
from .remote_timeline_cache import project_timeline_write

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
            result = instance._project._call(
                "timeline",
                self.definition.name,
                instance.sequence_id,
                *args,
                **kwargs,
            )
            if self.definition.access == "write":
                instance._apply_write(self.definition.name, result)
            return result

        return invoke


class RemoteTimelineEditor(_TimelineCommandSurface):
    snap_frame = staticmethod(snap_frame)

    def __init__(self, project: RemoteEditorProject, sequence_id: str):
        self._project = project
        self.sequence_id = sequence_id
        self._cached_state: TimelineState | None = None
        self._cached_revision = -1
        self._cached_duration_frames: int | None = None

    @property
    def state(self):
        revision = self._project.known_content_revision
        if self._cached_state is None or self._cached_revision != revision:
            value = self._project._call("timeline", "state", self.sequence_id)
            if not isinstance(value, TimelineState):
                raise RuntimeError("Editor Service returned an invalid timeline state")
            self._cached_state = value
            self._cached_revision = self._project.known_content_revision
            self._cached_duration_frames = value.duration_frames
        return self._cached_state

    @property
    def duration_frames(self) -> int:
        state = self.state
        if self._cached_duration_frames is None:
            self._cached_duration_frames = state.duration_frames
        return self._cached_duration_frames

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
        self._cached_duration_frames = value.duration_frames
        return value

    def invalidate(self) -> None:
        self._cached_state = None
        self._cached_revision = -1
        self._cached_duration_frames = None

    def _apply_write(self, command: str, result: Any) -> None:
        state = self._cached_state
        if state is None:
            return
        projected = project_timeline_write(state, command, result)
        if projected is None:
            self.invalidate()
            return
        self._cached_state = projected
        self._cached_revision = self._project.known_content_revision
        self._cached_duration_frames = projected.duration_frames


def _install_timeline_commands() -> None:
    for (target, name), _definition in DESKTOP_COMMANDS.items():
        if target != "timeline" or name in RemoteTimelineEditor.__dict__:
            continue
        descriptor = _RemoteTimelineMethod(name)
        descriptor.__set_name__(RemoteTimelineEditor, name)
        setattr(RemoteTimelineEditor, name, descriptor)


_install_timeline_commands()
