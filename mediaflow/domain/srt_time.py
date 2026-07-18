from __future__ import annotations

from fractions import Fraction


def format_srt_timestamp(value: Fraction) -> str:
    total_ms = round(float(value) * 1_000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
