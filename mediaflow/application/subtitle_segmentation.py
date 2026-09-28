from __future__ import annotations

import re

from mediaflow.domain.model_base import new_id
from mediaflow.domain.subtitles import SubtitleSegment, SubtitleWord

from .subtitle_word_timing import estimate_subtitle_words


def _text_key(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _too_long(text: str, limit: int) -> bool:
    if re.search(r"[\u3000-\u303f\u3040-\u30ff\uff00-\uffef\u3400-\u9fff]", text):
        return len(text) > limit
    return len(text) > max(1, round(limit * 56 / 24)) or len(text.split()) > max(1, round(limit * 11 / 24))


def aligned_word_boundaries(
    segment: SubtitleSegment,
    words: list[SubtitleWord],
) -> list[tuple[int, int, int]]:
    """Return (text offset, word count, frame) only for matching, separable words."""
    text = segment.text.strip()
    offsets = [index + 1 for index, char in enumerate(text) if not char.isspace()]
    if _text_key(text) != "".join(_text_key(word.text) for word in words):
        return []
    if len(words) < 2:
        return []
    right_starts = [0] * len(words)
    earliest = segment.end_frame
    for index in range(len(words) - 1, -1, -1):
        earliest = min(earliest, words[index].start_frame)
        right_starts[index] = earliest
    consumed = 0
    left_end = segment.start_frame
    boundaries = []
    for count, word in enumerate(words[:-1], 1):
        consumed += len(_text_key(word.text))
        left_end = max(left_end, word.end_frame)
        right_start = right_starts[count]
        if not 0 < consumed < len(offsets) or left_end > right_start:
            continue
        frame = (left_end + right_start) // 2
        if segment.start_frame < frame < segment.end_frame:
            boundaries.append((offsets[consumed - 1], count, frame))
    return boundaries


def split_for_readability(
    segment: SubtitleSegment,
    words: list[SubtitleWord],
    *,
    text_limit: int,
) -> list[tuple[SubtitleSegment, list[SubtitleWord]]]:
    """Split all oversized descendants in one edit, preserving recognized timing."""
    limit = max(1, int(text_limit))
    pending = [(segment, sorted(words, key=lambda word: word.position))]
    output = []
    while pending:
        current, current_words = pending.pop()
        text = current.text.strip()
        if not _too_long(text, limit) or current.end_frame - current.start_frame < 2:
            output.append((current, current_words))
            continue
        boundaries = aligned_word_boundaries(current, current_words)
        if not boundaries:
            # Old SRT or edited text has no trustworthy alignment. Never label
            # proportional timing as recognized, or erase excluded word edits.
            if any(word.excluded for word in current_words):
                output.append((current, current_words))
                continue
            current_words = estimate_subtitle_words(current)
            boundaries = aligned_word_boundaries(current, current_words)
        if not boundaries:
            output.append((current, current_words))
            continue
        # Prefer punctuation close to the middle. Distant punctuation must not
        # produce one huge remainder or one-character fragments at every step.
        middle = len(text) / 2
        nearby = [item for item in boundaries if abs(item[0] - middle) <= len(text) * 0.2]
        candidates = nearby or boundaries
        offset, count, frame = min(
            candidates,
            key=lambda item: (
                0 if text[item[0] - 1] in "。！？!?；;，,：:、." else 1,
                abs(item[0] - middle),
            ),
        )
        first = current.model_copy(update={"text": text[:offset].rstrip(), "end_frame": frame})
        second = current.model_copy(
            update={
                "id": new_id(),
                "text": text[offset:].lstrip(),
                "start_frame": frame,
            }
        )
        left = [
            word.model_copy(update={"segment_id": first.id, "position": index})
            for index, word in enumerate(current_words[:count])
        ]
        right = [
            word.model_copy(update={"segment_id": second.id, "position": index})
            for index, word in enumerate(current_words[count:])
        ]
        pending.extend(((second, right), (first, left)))
    return output
