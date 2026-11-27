from __future__ import annotations

import pytest

from mediaflow.application.keyframe_sequence_editing import (
    move_timeline_keyframe,
    paste_timeline_keyframes,
    retime_timeline_keyframes,
)
from mediaflow.domain.masks import MaskGeometry, MaskKeyframe
from mediaflow.domain.timeline import ClipTransform, ClipTransformKeyframe
from mediaflow.domain.visual_effects import VisualEffectParameterKeyframe


def _require_offset(offset: int) -> None:
    if not 0 <= offset < 20:
        raise ValueError("outside clip")


@pytest.mark.parametrize("kind", ["mask", "transform", "effect"])
def test_keyframe_movement_and_retiming_preserve_values_and_reject_collisions(kind: str) -> None:
    def keyframe(offset: int):
        if kind == "mask":
            return MaskKeyframe(timeline_offset=offset, geometry=MaskGeometry(center_x=0.3))
        if kind == "transform":
            return ClipTransformKeyframe(timeline_offset=offset, transform=ClipTransform(x=17))
        return VisualEffectParameterKeyframe(timeline_offset=offset, value=0.4)

    original = [keyframe(2), keyframe(8), keyframe(12)]
    moved = move_timeline_keyframe(
        original,
        8,
        10,
        validate_offset=_require_offset,
        occupied_message="occupied",
    )
    assert [item.timeline_offset for item in moved] == [2, 10, 12]
    assert moved[1].model_dump(exclude={"timeline_offset"}) == original[1].model_dump(
        exclude={"timeline_offset"},
    )
    assert original[1].timeline_offset == 8
    retimed = retime_timeline_keyframes(
        original,
        [8, 12],
        anchor_offset=2,
        scale=0.5,
        validate_offset=_require_offset,
        empty_message="empty",
        occupied_message="occupied",
    )
    assert [item.timeline_offset for item in retimed] == [2, 5, 7]
    with pytest.raises(ValueError, match="occupied"):
        move_timeline_keyframe(original, 8, 12, validate_offset=_require_offset, occupied_message="occupied")
    with pytest.raises(ValueError, match="occupied"):
        retime_timeline_keyframes(
            original,
            [8],
            anchor_offset=12,
            scale=0.1,
            validate_offset=_require_offset,
            empty_message="empty",
            occupied_message="occupied",
        )
    with pytest.raises(ValueError, match="outside clip"):
        move_timeline_keyframe(original, 8, 20, validate_offset=_require_offset, occupied_message="occupied")
    with pytest.raises(KeyError):
        retime_timeline_keyframes(
            original,
            [19],
            anchor_offset=0,
            scale=1,
            validate_offset=_require_offset,
            empty_message="empty",
            occupied_message="occupied",
        )


@pytest.mark.parametrize("kind", ["transform", "effect"])
def test_pasting_keyframes_replaces_colliding_offsets_and_preserves_copied_values(kind: str) -> None:
    def keyframe(offset: int, value: float):
        if kind == "transform":
            return ClipTransformKeyframe(timeline_offset=offset, transform=ClipTransform(x=value))
        return VisualEffectParameterKeyframe(timeline_offset=offset, value=value)

    existing = [keyframe(0, 0), keyframe(5, 1), keyframe(9, 2)]
    source = [keyframe(2, 10), keyframe(4, 20)]
    pasted = paste_timeline_keyframes(
        existing,
        source,
        destination_offset=5,
        scale=2,
        validate_offset=_require_offset,
        prepare=lambda item, offset: item.model_copy(update={"timeline_offset": offset}),
    )

    assert [item.timeline_offset for item in pasted] == [0, 5, 9]
    assert [item.model_dump(exclude={"timeline_offset"}) for item in pasted[1:]] == [
        item.model_dump(exclude={"timeline_offset"}) for item in source
    ]
    assert [item.timeline_offset for item in existing] == [0, 5, 9]
    assert [item.timeline_offset for item in source] == [2, 4]

    rounded = paste_timeline_keyframes(
        existing,
        source,
        destination_offset=5,
        scale=0.1,
        validate_offset=_require_offset,
        prepare=lambda item, offset: item.model_copy(update={"timeline_offset": offset}),
    )
    assert rounded[1].model_dump(exclude={"timeline_offset"}) == source[-1].model_dump(
        exclude={"timeline_offset"},
    )


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_pasting_keyframes_rejects_invalid_scale_before_preparing_points(scale: float) -> None:
    source = [VisualEffectParameterKeyframe(timeline_offset=2, value=0.4)]

    def unexpected_prepare(*_args):
        raise AssertionError("Invalid paste must not prepare or apply keyframes")

    with pytest.raises(ValueError, match="缩放必须大于零"):
        paste_timeline_keyframes(
            [],
            source,
            destination_offset=5,
            scale=scale,
            validate_offset=_require_offset,
            prepare=unexpected_prepare,
        )
