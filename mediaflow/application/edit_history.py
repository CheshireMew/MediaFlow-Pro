from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mediaflow.domain.collaboration import ProjectEditAction, ProjectEditCommand


@dataclass(frozen=True, slots=True)
class ProjectEditHistoryCheckpoint:
    undo: tuple[ProjectEditCommand, ...]
    redo: tuple[ProjectEditCommand, ...]


class ProjectEditHistory:
    """Serializable edit history shared by timeline and subtitle editing.

    Actions are data.  Active domain services register the handlers that apply
    those actions, so persisted commands can be rehydrated after a service
    restart without retaining per-edit Python closures.
    """

    def __init__(
        self,
        executor: Callable[[ProjectEditAction], None] | None = None,
    ) -> None:
        self._undo: list[ProjectEditCommand] = []
        self._redo: list[ProjectEditCommand] = []
        self._executor = executor
        self._handlers: dict[str, Callable[[ProjectEditAction], None]] = {}

    def register_handler(
        self,
        kind: str,
        handler: Callable[[ProjectEditAction], None],
    ) -> None:
        value = kind.strip()
        if not value:
            raise ValueError("Edit action kind is required")
        self._handlers[value] = handler

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def push(self, command: ProjectEditCommand) -> None:
        self._undo.append(command)
        self._redo.clear()

    def commands_since(
        self,
        checkpoint: ProjectEditHistoryCheckpoint,
    ) -> tuple[ProjectEditCommand, ...]:
        prefix_length = len(checkpoint.undo)
        if tuple(self._undo[:prefix_length]) != checkpoint.undo:
            raise RuntimeError("Edit history changed outside the active operation")
        return tuple(self._undo[prefix_length:])

    def combined_since(
        self,
        checkpoint: ProjectEditHistoryCheckpoint,
        *,
        label: str | None = None,
    ) -> ProjectEditCommand | None:
        commands = self.commands_since(checkpoint)
        if not commands:
            return None
        return ProjectEditCommand(
            label=(label or commands[-1].label).strip(),
            undo_actions=[
                action
                for command in reversed(commands)
                for action in command.undo_actions
            ],
            redo_actions=[
                action
                for command in commands
                for action in command.redo_actions
            ],
        )

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

    def squash_since(
        self,
        checkpoint: ProjectEditHistoryCheckpoint,
        *,
        label: str,
    ) -> None:
        prefix_length = len(checkpoint.undo)
        command = self.combined_since(checkpoint, label=label)
        if command is None:
            return
        self._undo[prefix_length:] = [command]
        self._redo.clear()

    def undo(self) -> str:
        if not self._undo:
            raise RuntimeError("Nothing to undo")
        command = self._undo[-1]
        self.apply_actions(command.undo_actions)
        self._undo.pop()
        self._redo.append(command)
        return command.label

    def redo(self) -> str:
        if not self._redo:
            raise RuntimeError("Nothing to redo")
        command = self._redo[-1]
        self.apply_actions(command.redo_actions)
        self._redo.pop()
        self._undo.append(command)
        return command.label

    def apply_actions(self, actions: list[ProjectEditAction]) -> None:
        for action in actions:
            handler = self._handlers.get(action.kind)
            if handler is not None:
                handler(action)
            elif self._executor is not None:
                self._executor(action)
            else:
                raise RuntimeError(
                    f"No active domain owner can apply edit action {action.kind!r}"
                )

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
