from __future__ import annotations

from fractions import Fraction
from typing import Protocol


class FrameRate(Protocol):
    fps_numerator: int
    fps_denominator: int


def fps_fraction(numerator: int, denominator: int) -> Fraction:
    if numerator <= 0 or denominator <= 0:
        raise ValueError("Frame rate numerator and denominator must be positive")
    return Fraction(numerator, denominator)


def seconds_to_frames(
    seconds: int | float | Fraction,
    fps_numerator: int,
    fps_denominator: int,
) -> int:
    """Round media time to the nearest project frame without float storage."""
    value = seconds if isinstance(seconds, Fraction) else Fraction(str(seconds))
    frames = value * fps_fraction(fps_numerator, fps_denominator)
    quotient, remainder = divmod(frames.numerator, frames.denominator)
    return quotient + (1 if remainder * 2 >= frames.denominator else 0)


def frames_to_seconds(
    frames: int,
    fps_numerator: int,
    fps_denominator: int,
) -> Fraction:
    return Fraction(frames, 1) / fps_fraction(fps_numerator, fps_denominator)


def reframe_frames(frames: int, source: FrameRate, destination: FrameRate) -> int:
    """Preserve media time while moving an integer frame value between clocks."""
    return reframe_rate(
        frames,
        source.fps_numerator,
        source.fps_denominator,
        destination.fps_numerator,
        destination.fps_denominator,
    )


def reframe_rate(
    frames: int,
    source_numerator: int,
    source_denominator: int,
    destination_numerator: int,
    destination_denominator: int,
) -> int:
    if source_numerator == destination_numerator and source_denominator == destination_denominator:
        return frames
    return seconds_to_frames(
        frames_to_seconds(frames, source_numerator, source_denominator),
        destination_numerator,
        destination_denominator,
    )


def source_frames_for_timeline_frames(
    timeline_frames: int,
    speed_numerator: int,
    speed_denominator: int,
) -> int:
    if speed_denominator <= 0 or speed_numerator == 0:
        raise ValueError("Speed must be non-zero and have a positive denominator")
    source = Fraction(timeline_frames * abs(speed_numerator), speed_denominator)
    quotient, remainder = divmod(source.numerator, source.denominator)
    return quotient + (1 if remainder * 2 >= source.denominator else 0)
