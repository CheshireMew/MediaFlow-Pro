from __future__ import annotations

import re

from mediaflow.application.subtitle_segmentation import split_for_readability
from mediaflow.domain.subtitles import SubtitleSegment, SubtitleWord


def test_1008_character_paragraph_is_split_completely_in_one_operation():
    segment = SubtitleSegment(
        document_id="doc", start_frame=0, end_frame=4800, text="这是没有标点的播客内容" * 92
    )
    parts = split_for_readability(segment, [], text_limit=28)
    assert len(parts) > 32
    assert all(len(part.text) <= 28 for part, _ in parts)
    assert "".join(part.text for part, _ in parts) == segment.text
    assert parts[0][0].id == segment.id
    assert parts[-1][0].end_frame == segment.end_frame
    for part, words in parts:
        assert "".join(word.text for word in words) == part.text
        assert all(word.timing_source == "estimated" for word in words)
        assert all(part.start_frame <= word.start_frame < word.end_frame <= part.end_frame for word in words)
    assert all(split_for_readability(part, words, text_limit=28) == [(part, words)] for part, words in parts)


def test_word_text_boundary_drives_time_and_ownership():
    segment = SubtitleSegment(
        document_id="doc", start_frame=0, end_frame=120, text="甲乙丙丁", speaker="主持人"
    )
    words = [
        SubtitleWord(segment_id=segment.id, position=index, text=char, start_frame=start, end_frame=end)
        for index, (char, start, end) in enumerate([("甲", 0, 1), ("乙", 1, 2), ("丙", 2, 3), ("丁", 3, 120)])
    ]
    parts = split_for_readability(segment, words, text_limit=2)
    assert [(part.text, part.start_frame, part.end_frame) for part, _ in parts] == [
        ("甲乙", 0, 2),
        ("丙丁", 2, 120),
    ]
    assert [[word.text for word in group] for _, group in parts] == [["甲", "乙"], ["丙", "丁"]]
    assert [word.id for _, group in parts for word in group] == [word.id for word in words]
    assert all(part.speaker == "主持人" for part, _ in parts)
    assert all(word.timing_source == "recognized" for _, group in parts for word in group)


def test_english_keeps_whole_words_and_marks_missing_alignment_estimated():
    segment = SubtitleSegment(
        document_id="doc",
        start_frame=0,
        end_frame=900,
        text="These are complete English words without punctuation " * 12,
    )
    parts = split_for_readability(segment, [], text_limit=24)
    assert " ".join(part.text for part, _ in parts).split() == segment.text.split()
    assert all(len(part.text.split()) <= 11 for part, _ in parts)
    assert all(re.sub(r"\s", "", part.text) == "".join(word.text for word in words) for part, words in parts)


def test_dense_timing_does_not_create_invalid_frame_ranges():
    segment = SubtitleSegment(document_id="doc", start_frame=0, end_frame=2, text="很快的一段话" * 10)
    parts = split_for_readability(segment, [], text_limit=2)
    assert "".join(part.text for part, _ in parts) == segment.text
    assert all(part.start_frame < part.end_frame for part, _ in parts)
    assert all(
        part.start_frame <= word.start_frame < word.end_frame <= part.end_frame
        for part, words in parts
        for word in words
    )
