from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from mediaflow.domain.collaboration import (
    ProjectChangeEvent,
    ProjectRevisionConflict,
    project_write_paths_overlap,
)


@dataclass(frozen=True, slots=True)
class ProjectRevisionResolution:
    effective_revision: int
    rebased_from: int | None


def resolve_project_revision(
    *,
    base_revision: int | None,
    current_revision: int,
    write_set: list[str],
    events: Iterable[ProjectChangeEvent],
    conflict_reason: str,
) -> ProjectRevisionResolution:
    """Resolve one project write against the durable event journal."""

    if base_revision is None:
        raise ValueError("base_revision is required for project writes")
    if base_revision == current_revision:
        return ProjectRevisionResolution(
            effective_revision=current_revision,
            rebased_from=None,
        )
    if base_revision > current_revision:
        raise ProjectRevisionConflict(
            expected_revision=base_revision,
            current_revision=current_revision,
            write_set=write_set,
            conflicting_events=[],
            reason="the requested base revision is newer than the project",
        )

    covered_revision = base_revision
    conflicts: list[ProjectChangeEvent] = []
    for event in events:
        if event.base_revision != covered_revision:
            raise ProjectRevisionConflict(
                expected_revision=base_revision,
                current_revision=current_revision,
                write_set=write_set,
                conflicting_events=[],
                reason="the durable event journal does not cover the requested revision",
            )
        if any(project_write_paths_overlap(left, right) for left in write_set for right in event.write_set):
            conflicts.append(event)
        covered_revision = event.project_revision
    if covered_revision != current_revision:
        raise ProjectRevisionConflict(
            expected_revision=base_revision,
            current_revision=current_revision,
            write_set=write_set,
            conflicting_events=[],
            reason="the durable event journal does not reach the current revision",
        )
    if conflicts:
        raise ProjectRevisionConflict(
            expected_revision=base_revision,
            current_revision=current_revision,
            write_set=write_set,
            conflicting_events=conflicts,
            reason=conflict_reason,
        )
    return ProjectRevisionResolution(
        effective_revision=current_revision,
        rebased_from=base_revision,
    )
