from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from mediaflow.domain.web_state import WebEasing, WebKeyframe


def upsert_web_keyframe(
    keyframes: Sequence[WebKeyframe],
    *,
    time_ms: int,
    value: JsonValue,
    easing: Mapping[str, object] | None,
) -> list[WebKeyframe]:
    updated = [item for item in keyframes if item.time_ms != time_ms]
    updated.append(
        WebKeyframe(
            time_ms=time_ms,
            value=value,
            easing=WebEasing.model_validate(easing or {}),
        )
    )
    return sorted(updated, key=lambda item: item.time_ms)


def remove_web_keyframe(
    keyframes: Sequence[WebKeyframe],
    *,
    time_ms: int,
    missing_identity: str,
) -> list[WebKeyframe]:
    remaining = [item for item in keyframes if item.time_ms != time_ms]
    if len(remaining) == len(keyframes):
        raise KeyError(missing_identity)
    return remaining


def move_web_keyframe(
    keyframes: Sequence[WebKeyframe],
    *,
    old_time_ms: int,
    new_time_ms: int,
    missing_identity: str,
    occupied_message: str,
) -> list[WebKeyframe]:
    moving = next(
        (item for item in keyframes if item.time_ms == old_time_ms),
        None,
    )
    if moving is None:
        raise KeyError(missing_identity)
    if any(
        item.time_ms == new_time_ms and item.time_ms != old_time_ms
        for item in keyframes
    ):
        raise ValueError(occupied_message)
    updated = [item for item in keyframes if item.time_ms != old_time_ms]
    updated.append(moving.model_copy(update={"time_ms": new_time_ms}))
    return sorted(updated, key=lambda item: item.time_ms)
