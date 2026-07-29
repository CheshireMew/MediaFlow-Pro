from __future__ import annotations

import re
from collections.abc import Callable

_MELT_FRAME = re.compile(r"Current Frame:\s*(\d+)", re.IGNORECASE)
_MELT_PERCENT = re.compile(r"percentage:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


class MeltProgressObserver:
    def __init__(self, total_frames: int, on_frame: Callable[[float], None]):
        if total_frames <= 0:
            raise ValueError("MLT progress requires a positive frame count")
        self.total_frames = int(total_frames)
        self.on_frame = on_frame

    def __call__(self, line: str) -> None:
        frame_match = _MELT_FRAME.search(line)
        if frame_match:
            self.on_frame(min(self.total_frames, max(0, int(frame_match.group(1)) + 1)))
            return
        percent_match = _MELT_PERCENT.search(line)
        if percent_match:
            percent = min(100.0, max(0.0, float(percent_match.group(1))))
            self.on_frame(self.total_frames * percent / 100.0)
