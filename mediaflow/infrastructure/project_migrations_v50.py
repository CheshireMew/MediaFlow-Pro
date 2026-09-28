from __future__ import annotations

import json
import sqlite3
from typing import Any

from .project_snapshot_migration import migrate_version_snapshots

_DEFAULT_CURVE = {
    "interpolation": "linear",
    "x1": 0.25,
    "y1": 0.1,
    "x2": 0.25,
    "y2": 1.0,
}


def migrate_v49_to_v50(workspace) -> None:
    with workspace.transaction() as connection:
        migrate_version_snapshots(
            workspace,
            connection,
            source_version=49,
            target_version=50,
            migrate_database=_add_native_keyframe_curves,
        )
        _add_native_keyframe_curves(connection)
        connection.execute(
            "UPDATE schema_info SET version=50 WHERE component='project'"
        )


def _add_native_keyframe_curves(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT id, transform_keyframes_json, visual_effects_json FROM clip"
    ).fetchall()
    for row in rows:
        transform_keyframes = _json_array(row["transform_keyframes_json"], "transform keyframes")
        visual_effects = _json_array(row["visual_effects_json"], "visual effects")

        migrated_transform_keyframes = []
        for raw_keyframe in transform_keyframes:
            keyframe = _json_object(raw_keyframe, "transform keyframe")
            keyframe.setdefault("curve", dict(_DEFAULT_CURVE))
            migrated_transform_keyframes.append(keyframe)

        migrated_visual_effects = []
        for raw_effect in visual_effects:
            effect = _json_object(raw_effect, "visual effect")
            effect.setdefault("parameter_keyframes", {})
            parameter_keyframes = effect["parameter_keyframes"]
            if not isinstance(parameter_keyframes, dict):
                raise ValueError("Visual effect parameter keyframes must be an object")
            for field_id, raw_keyframes in parameter_keyframes.items():
                keyframes = _json_array(raw_keyframes, f"{field_id} parameter keyframes")
                migrated_keyframes = []
                for raw_keyframe in keyframes:
                    keyframe = _json_object(raw_keyframe, "visual effect parameter keyframe")
                    keyframe.setdefault("curve", dict(_DEFAULT_CURVE))
                    migrated_keyframes.append(keyframe)
                parameter_keyframes[field_id] = migrated_keyframes
            migrated_visual_effects.append(effect)

        connection.execute(
            """UPDATE clip
               SET transform_keyframes_json=?, visual_effects_json=?
               WHERE id=?""",
            (
                _canonical_json(migrated_transform_keyframes),
                _canonical_json(migrated_visual_effects),
                row["id"],
            ),
        )


def _json_array(value: Any, label: str) -> list[Any]:
    parsed = json.loads(str(value)) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        raise ValueError(f"Persisted {label} must be an array")
    return parsed


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Persisted {label} must be an object")
    return dict(value)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
