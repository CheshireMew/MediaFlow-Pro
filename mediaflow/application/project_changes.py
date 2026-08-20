from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mediaflow.domain.collaboration import ProjectChange, ProjectChangeSet
from mediaflow.domain.timeline import TimelineState


def project_path_segment(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def timeline_change_set(
    before: TimelineState,
    after: TimelineState,
) -> ProjectChangeSet:
    """Describe the real semantic difference between two timeline snapshots."""

    if before.sequence.id != after.sequence.id:
        raise ValueError("Timeline snapshots must belong to the same sequence")
    root = f"/sequences/{project_path_segment(before.sequence.id)}"
    changes: list[ProjectChange] = []
    _diff_value(
        f"{root}/settings",
        _model_document(before.sequence, exclude={"timeline_revision"}),
        _model_document(after.sequence, exclude={"timeline_revision"}),
        changes,
    )
    for name, before_items, after_items, ordered in (
        ("tracks", before.tracks, after.tracks, True),
        ("clips", before.clips, after.clips, False),
        ("compounds", before.compounds, after.compounds, False),
        ("transitions", before.transitions, after.transitions, False),
        ("markers", before.markers, after.markers, False),
        ("ranges", before.ranges, after.ranges, False),
    ):
        _diff_entity_sequence(
            f"{root}/{name}",
            before_items,
            after_items,
            changes,
            ordered=ordered,
        )
    _diff_entity_mapping(f"{root}/web-states", before.web_states, after.web_states, changes)
    return ProjectChangeSet(changes=sorted(changes, key=lambda item: item.path))


def entity_sequence_change_set(
    root: str,
    before: Sequence[Any],
    after: Sequence[Any],
) -> ProjectChangeSet:
    changes: list[ProjectChange] = []
    _diff_entity_sequence(root.rstrip("/"), before, after, changes)
    return ProjectChangeSet(changes=sorted(changes, key=lambda item: item.path))


def entity_change_set(root: str, before: Any, after: Any) -> ProjectChangeSet:
    changes: list[ProjectChange] = []
    _diff_value(
        root.rstrip("/"),
        _model_document(before),
        _model_document(after),
        changes,
    )
    return ProjectChangeSet(changes=sorted(changes, key=lambda item: item.path))


def value_change_set(root: str, before: Any, after: Any) -> ProjectChangeSet:
    """Diff two already-serialized values at one observable project path."""

    changes: list[ProjectChange] = []
    _diff_value(root.rstrip("/"), before, after, changes)
    return ProjectChangeSet(changes=sorted(changes, key=lambda item: item.path))


def _diff_entity_sequence(
    root: str,
    before: Sequence[Any],
    after: Sequence[Any],
    changes: list[ProjectChange],
    *,
    ordered: bool = False,
) -> None:
    before_by_id = {_entity_id(item): item for item in before}
    after_by_id = {_entity_id(item): item for item in after}
    _diff_entity_mapping(root, before_by_id, after_by_id, changes)
    before_order = list(before_by_id)
    after_order = list(after_by_id)
    if ordered and before_order != after_order:
        changes.append(
            ProjectChange(
                path=f"{root}/order",
                action="update",
                value=after_order,
            )
        )


def _diff_entity_mapping(
    root: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    changes: list[ProjectChange],
) -> None:
    before_ids = set(before)
    after_ids = set(after)
    for entity_id in sorted(before_ids - after_ids):
        changes.append(ProjectChange(path=_join(root, entity_id), action="delete"))
    for entity_id in sorted(after_ids - before_ids):
        changes.append(
            ProjectChange(
                path=_join(root, entity_id),
                action="create",
                value=_model_document(after[entity_id]),
            )
        )
    for entity_id in sorted(before_ids & after_ids):
        _diff_value(
            _join(root, entity_id),
            _model_document(before[entity_id]),
            _model_document(after[entity_id]),
            changes,
        )


def _diff_value(
    path: str,
    before: Any,
    after: Any,
    changes: list[ProjectChange],
) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        before_keys = set(before)
        after_keys = set(after)
        for key in sorted(before_keys - after_keys):
            changes.append(ProjectChange(path=_join(path, key), action="delete"))
        for key in sorted(after_keys - before_keys):
            changes.append(
                ProjectChange(
                    path=_join(path, key),
                    action="create",
                    value=after[key],
                )
            )
        for key in sorted(before_keys & after_keys):
            _diff_value(_join(path, key), before[key], after[key], changes)
        return
    changes.append(ProjectChange(path=path, action="update", value=after))


def _entity_id(value: Any) -> str:
    entity_id = next(
        (
            candidate
            for name in ("id", "asset_id", "track_id")
            if isinstance((candidate := getattr(value, name, None)), str) and candidate
        ),
        None,
    )
    if not isinstance(entity_id, str) or not entity_id:
        raise TypeError(f"Project change entity has no stable id: {type(value)!r}")
    return entity_id


def _model_document(value: Any, *, exclude: set[str] | None = None) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(
            mode="json",
            exclude=exclude,
            exclude_computed_fields=True,
        )
    return value


def _join(root: str, value: object) -> str:
    return f"{root}/{project_path_segment(value)}"
