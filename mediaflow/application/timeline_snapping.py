from __future__ import annotations

from collections.abc import Iterable


def snap_frame(
    frame: int,
    targets: Iterable[int],
    tolerance_frames: int,
) -> int:
    if frame < 0:
        raise ValueError("Timeline frame cannot be negative")
    if tolerance_frames < 0:
        raise ValueError("Snap tolerance cannot be negative")
    candidates = [
        target
        for target in targets
        if abs(target - frame) <= tolerance_frames
    ]
    if not candidates:
        return frame
    return min(candidates, key=lambda target: (abs(target - frame), target))
