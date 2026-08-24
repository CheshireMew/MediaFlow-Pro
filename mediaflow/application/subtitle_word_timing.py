from __future__ import annotations

import re

from mediaflow.domain.subtitles import SubtitleSegment, SubtitleWord

_SUBTITLE_TOKEN_PATTERN = re.compile(
    r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|[^\s]"
)


def estimate_subtitle_words(segment: SubtitleSegment) -> list[SubtitleWord]:
    """Project plain subtitle text onto deterministic, evenly spaced word timing."""

    tokens = _SUBTITLE_TOKEN_PATTERN.findall(segment.text)
    if not tokens:
        return []
    duration = segment.end_frame - segment.start_frame
    return [
        SubtitleWord(
            segment_id=segment.id,
            position=position,
            start_frame=segment.start_frame + duration * position // len(tokens),
            end_frame=max(
                segment.start_frame + duration * position // len(tokens) + 1,
                segment.start_frame + duration * (position + 1) // len(tokens),
            ),
            text=token,
            timing_source="estimated",
        )
        for position, token in enumerate(tokens)
    ]
