from __future__ import annotations

import re
from collections.abc import Callable, Sequence

_MELT_FRAME = re.compile(r"Current Frame:\s*(\d+)", re.IGNORECASE)
_MELT_PERCENT = re.compile(r"percentage:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def ffmpeg_progress_command(command: Sequence[str]) -> list[str]:
    if not command:
        raise ValueError("FFmpeg command cannot be empty")
    return [str(command[0]), "-nostats", "-progress", "pipe:2", *map(str, command[1:])]


class FfmpegProgressObserver:
    def __init__(self, total_seconds: float, on_position: Callable[[float], None]):
        if total_seconds <= 0:
            raise ValueError("FFmpeg progress requires a positive media duration")
        self.total_seconds = float(total_seconds)
        self.on_position = on_position

    def __call__(self, line: str) -> None:
        key, separator, value = line.partition("=")
        if not separator:
            return
        seconds: float | None = None
        if key in {"out_time_us", "out_time_ms"}:
            try:
                seconds = int(value) / 1_000_000.0
            except ValueError:
                return
        elif key == "out_time":
            parts = value.split(":")
            if len(parts) != 3:
                return
            try:
                hours, minutes, raw_seconds = parts
                seconds = int(hours) * 3600 + int(minutes) * 60 + float(raw_seconds)
            except ValueError:
                return
        elif key == "progress" and value == "end":
            seconds = self.total_seconds
        if seconds is not None:
            self.on_position(max(0.0, min(self.total_seconds, seconds)))


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
