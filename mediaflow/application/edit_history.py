from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectEditCommand:
    label: str
    undo_action: Callable[[], None]
    redo_action: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ProjectEditHistoryCheckpoint:
    undo: tuple[ProjectEditCommand, ...]
    redo: tuple[ProjectEditCommand, ...]


class ProjectEditHistory:
    """One chronological session history shared by timeline and subtitle editing."""

    def __init__(self) -> None:
        self._undo: list[ProjectEditCommand] = []
        self._redo: list[ProjectEditCommand] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def push(self, command: ProjectEditCommand) -> None:
        self._undo.append(command)
        self._redo.clear()

    def checkpoint(self) -> ProjectEditHistoryCheckpoint:
        return ProjectEditHistoryCheckpoint(
            undo=tuple(self._undo),
            redo=tuple(self._redo),
        )

    def restore(
        self,
        checkpoint: ProjectEditHistoryCheckpoint,
    ) -> None:
        self._undo = list(checkpoint.undo)
        self._redo = list(checkpoint.redo)

    def undo(self) -> str:
        if not self._undo:
            raise RuntimeError("Nothing to undo")
        command = self._undo[-1]
        command.undo_action()
        self._undo.pop()
        self._redo.append(command)
        return command.label

    def redo(self) -> str:
        if not self._redo:
            raise RuntimeError("Nothing to redo")
        command = self._redo[-1]
        command.redo_action()
        self._redo.pop()
        self._undo.append(command)
        return command.label

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
