from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from mediaflow.domain.model_base import DomainModel

UndoGroupState = Literal["applied", "undone", "discarded"]
ActiveUndoGroupState = Literal["applied", "undone"]


@dataclass(frozen=True, slots=True)
class ProjectWritePath:
    value: str
    segments: tuple[str, ...]

    @classmethod
    def parse(cls, value: str) -> ProjectWritePath:
        if not isinstance(value, str):
            raise TypeError("Project write path must be a string")
        normalized = value.rstrip("/")
        if not normalized:
            if value.startswith("/"):
                return cls(value="/", segments=())
            raise ValueError("Project write path must be absolute")
        if not normalized.startswith("/"):
            raise ValueError("Project write path must be absolute")
        segments = tuple(normalized[1:].split("/"))
        if any(not segment or segment in {".", ".."} for segment in segments):
            raise ValueError("Project write path contains an invalid segment")
        return cls(value=normalized, segments=segments)

    def overlaps(self, other: ProjectWritePath) -> bool:
        shared = min(len(self.segments), len(other.segments))
        return self.segments[:shared] == other.segments[:shared]


def project_write_paths_overlap(left: str, right: str) -> bool:
    return ProjectWritePath.parse(left).overlaps(ProjectWritePath.parse(right))


def project_write_path_covers(scope: str, path: str) -> bool:
    """Return whether a declared write scope contains one concrete change path."""

    planned = ProjectWritePath.parse(scope)
    changed = ProjectWritePath.parse(path)
    return changed.segments[: len(planned.segments)] == planned.segments


class ActorIdentity(DomainModel):
    kind: Literal["human", "agent", "automation", "system"]
    id: str = Field(min_length=1)
    name: str = ""


class ProjectChange(DomainModel):
    path: str = Field(min_length=1)
    action: Literal["create", "update", "delete"]
    value: Any = None


class ProjectChangeSet(DomainModel):
    """Canonical, observable project changes produced by one committed command."""

    changes: list[ProjectChange] = Field(default_factory=list)

    @property
    def write_set(self) -> list[str]:
        return sorted({change.path for change in self.changes})

    @classmethod
    def combine(cls, values: list[ProjectChangeSet]) -> ProjectChangeSet:
        combined: dict[str, ProjectChange] = {}
        for value in values:
            for change in value.changes:
                combined[change.path] = change
        return cls(changes=[combined[path] for path in sorted(combined)])


class ProjectMutationPlan(DomainModel):
    """Conflict footprint plus the complete scope an operation may mutate."""

    conflict_set: list[str]
    change_scopes: list[str]

    @classmethod
    def scoped(
        cls,
        scopes: list[str],
        *,
        conflict_set: list[str] | None = None,
    ) -> ProjectMutationPlan:
        return cls(
            conflict_set=sorted(set(conflict_set if conflict_set is not None else scopes)),
            change_scopes=sorted(set(scopes)),
        )


class ProjectEditAction(DomainModel):
    kind: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ProjectEditCommand(DomainModel):
    label: str = Field(min_length=1)
    undo_actions: list[ProjectEditAction] = Field(min_length=1)
    redo_actions: list[ProjectEditAction] = Field(min_length=1)


class ProjectUndoGroup(DomainModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    source_revision: int = Field(ge=0)
    state_revision: int = Field(ge=0)
    label: str = Field(min_length=1)
    actor: ActorIdentity
    write_set: list[str]
    command: ProjectEditCommand
    state: UndoGroupState
    created_at: int
    updated_at: int


class ProjectChangeEvent(DomainModel):
    cursor: int = Field(ge=1)
    project_id: str
    project_path: str
    base_revision: int = Field(ge=0)
    project_revision: int = Field(ge=0)
    operation: str
    actor: ActorIdentity
    request_id: str
    undo_group_id: str
    write_set: list[str]
    changes: list[ProjectChange]
    operation_result: dict[str, Any]
    inverse_command: ProjectEditCommand | None = None
    created_at: int


class ProjectRevisionConflict(RuntimeError):
    def __init__(
        self,
        *,
        expected_revision: int,
        current_revision: int,
        write_set: list[str],
        conflicting_events: list[ProjectChangeEvent],
        reason: str,
    ):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        self.write_set = write_set
        self.conflicting_events = conflicting_events
        self.reason = reason
        super().__init__(
            f"Project revision conflict: expected {expected_revision}, current {current_revision}; {reason}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "expected_revision": self.expected_revision,
            "current_revision": self.current_revision,
            "write_set": self.write_set,
            "reason": self.reason,
            "conflicting_events": [event.model_dump(mode="json") for event in self.conflicting_events],
        }
