from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mediaflow.application.project_changes import value_change_set
from mediaflow.domain.collaboration import ProjectChange, ProjectChangeSet


@dataclass(frozen=True, slots=True)
class ObservedProjectValue:
    exists: bool
    value: Any = None


@dataclass(frozen=True, slots=True)
class ProjectObservation:
    values: dict[str, ObservedProjectValue]

    def changes_to(self, destination: ProjectObservation) -> ProjectChangeSet:
        if set(self.values) != set(destination.values):
            raise ValueError("Project observations must cover the same paths")
        change_sets: list[ProjectChangeSet] = []
        for path in sorted(self.values):
            before = self.values[path]
            after = destination.values[path]
            if before == after:
                continue
            if not before.exists:
                change_sets.append(
                    ProjectChangeSet(changes=[ProjectChange(path=path, action="create", value=after.value)])
                )
            elif not after.exists:
                change_sets.append(ProjectChangeSet(changes=[ProjectChange(path=path, action="delete")]))
            else:
                change_sets.append(value_change_set(path, before.value, after.value))
        return ProjectChangeSet.combine(change_sets)
