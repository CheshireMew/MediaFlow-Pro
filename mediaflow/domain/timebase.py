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


def round_fraction(value: Fraction) -> int:
    """Round to the nearest integer, with exact halves away from zero."""

    sign = -1 if value < 0 else 1
    magnitude = abs(value)
    quotient, remainder = divmod(magnitude.numerator, magnitude.denominator)
    rounded = quotient + (1 if remainder * 2 >= magnitude.denominator else 0)
    return sign * rounded


def seconds_to_frames(
    seconds: int | float | Fraction,
    fps_numerator: int,
    fps_denominator: int,
) -> int:
    """Round media time to the nearest project frame without float storage."""
    value = seconds if isinstance(seconds, Fraction) else Fraction(str(seconds))
    frames = value * fps_fraction(fps_numerator, fps_denominator)
    return round_fraction(frames)


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


def reframe_interval(
    start_frame: int,
    end_frame: int,
    source: FrameRate,
    destination: FrameRate,
) -> tuple[int, int]:
    """Move a half-open frame interval without losing covered media time."""
    return reframe_rate_interval(
        start_frame,
        end_frame,
        source.fps_numerator,
        source.fps_denominator,
        destination.fps_numerator,
        destination.fps_denominator,
    )


def reframe_rate_interval(
    start_frame: int,
    end_frame: int,
    source_numerator: int,
    source_denominator: int,
    destination_numerator: int,
    destination_denominator: int,
) -> tuple[int, int]:
    """Numeric counterpart of :func:`reframe_interval` for storage rows."""
    if end_frame <= start_frame:
        raise ValueError("Frame interval must contain at least one frame")
    scale = fps_fraction(
        destination_numerator,
        destination_denominator,
    ) / fps_fraction(
        source_numerator,
        source_denominator,
    )
    start = Fraction(start_frame) * scale
    end = Fraction(end_frame) * scale
    converted_start = start.numerator // start.denominator
    converted_end = -(-end.numerator // end.denominator)
    return converted_start, max(converted_start + 1, converted_end)


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
    return round_fraction(source)


def source_frame_at_timeline_offset(
    source_in: int,
    timeline_offset: int,
    speed_numerator: int,
    speed_denominator: int,
    *,
    freeze_source_frame: int | None = None,
) -> int:
    """Map a clip-local timeline frame to its deterministic source frame."""

    if source_in < 0 or timeline_offset < 0:
        raise ValueError("Source and timeline frame positions cannot be negative")
    if freeze_source_frame is not None:
        return freeze_source_frame
    consumed = source_frames_for_timeline_frames(
        timeline_offset,
        speed_numerator,
        speed_denominator,
    )
    source_frame = source_in + consumed if speed_numerator > 0 else source_in - consumed
    if source_frame < 0:
        raise ValueError("Timeline offset maps before the beginning of the source")
    return source_frame


def timeline_offset_for_source_frame(
    source_in: int,
    source_frame: int,
    speed_numerator: int,
    speed_denominator: int,
    *,
    freeze_source_frame: int | None = None,
) -> int:
    """Map a source frame back to a clip-local timeline offset."""

    if source_in < 0 or source_frame < 0:
        raise ValueError("Source frame positions cannot be negative")
    if freeze_source_frame is not None:
        if source_frame != freeze_source_frame:
            raise ValueError("A freeze clip only maps its frozen source frame")
        return 0
    delta = source_frame - source_in
    if (speed_numerator > 0 and delta < 0) or (speed_numerator < 0 and delta > 0):
        raise ValueError("Source frame lies outside the clip playback direction")
    speed = Fraction(abs(speed_numerator), speed_denominator)
    return round_fraction(Fraction(abs(delta), 1) / speed)


def source_interval_for_timeline_interval(
    source_in: int,
    timeline_start_offset: int,
    timeline_end_offset: int,
    speed_numerator: int,
    speed_denominator: int,
    *,
    freeze_source_frame: int | None = None,
) -> tuple[int, int]:
    """Return the half-open source interval covered by a timeline interval."""

    if timeline_end_offset <= timeline_start_offset:
        raise ValueError("Timeline interval must contain at least one frame")
    first = source_frame_at_timeline_offset(
        source_in,
        timeline_start_offset,
        speed_numerator,
        speed_denominator,
        freeze_source_frame=freeze_source_frame,
    )
    last = source_frame_at_timeline_offset(
        source_in,
        timeline_end_offset - 1,
        speed_numerator,
        speed_denominator,
        freeze_source_frame=freeze_source_frame,
    )
    return min(first, last), max(first, last) + 1
