from __future__ import annotations

from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.speech_review import SpeechReviewRules, review_transcript
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.domain.transcript_edits import TranscriptSegmentSnapshot, TranscriptSnapshot


def test_transcript_review_reports_restarts_fillers_and_suspicious_gaps_without_deleting() -> None:
    document = SubtitleDocument(
        id="transcript",
        project_id="project",
        asset_id="subtitle-asset",
        sequence_id="main",
        language="zh",
        purpose="sequence_transcript",
    )
    intro = SubtitleSegment(
        id="intro",
        document_id=document.id,
        start_frame=5,
        end_frame=10,
        text="嗯",
    )
    first = SubtitleSegment(
        id="first-take",
        document_id=document.id,
        start_frame=15,
        end_frame=40,
        text="这个功能我们应该这样处理",
    )
    second = SubtitleSegment(
        id="second-take",
        document_id=document.id,
        start_frame=65,
        end_frame=95,
        text="这个功能我们应该这样处理才更连贯",
    )
    snapshot = TranscriptSnapshot(
        content_revision=7,
        document=document,
        segments=[
            TranscriptSegmentSnapshot(
                segment=intro,
                words=[
                    SubtitleWord(
                        id="filler-word",
                        segment_id=intro.id,
                        position=0,
                        start_frame=5,
                        end_frame=10,
                        text="嗯",
                    )
                ],
            ),
            TranscriptSegmentSnapshot(segment=first),
            TranscriptSegmentSnapshot(segment=second),
        ],
        recognized_word_count=1,
        estimated_word_count=0,
    )

    review = review_transcript(
        snapshot,
        SpeechReviewRules(),
        frame_profile=ProjectProfile(fps_numerator=25, fps_denominator=1),
        timeline_duration_seconds=4.5,
    )

    assert review.similarity_candidate_count == 1
    assert review.filler_candidate_count == 1
    assert review.gap_candidate_count == 3
    assert review.candidate_count == 5
    assert review.blocks_production is True
    assert review.automatic_deletions == 0
    leading = next(candidate for candidate in review.candidates if candidate.kind == "leading_gap")
    assert "inhale" in leading.evidence
    assert leading.manual_review_required is True
    filler = next(candidate for candidate in review.candidates if candidate.kind == "filler")
    assert filler.selection_kind == "words"
    assert filler.selection_ids == ["filler-word"]
    assert filler.suggested_action == "transcript.edit.preview"


def test_transcript_review_can_return_a_standalone_filler_segment_without_word_timing() -> None:
    document = SubtitleDocument(
        id="transcript",
        project_id="project",
        asset_id="subtitle-asset",
        sequence_id="main",
        language="zh",
        purpose="sequence_transcript",
    )
    filler = SubtitleSegment(
        id="filler-segment",
        document_id=document.id,
        start_frame=0,
        end_frame=5,
        text="嗯嗯",
    )
    snapshot = TranscriptSnapshot(
        content_revision=1,
        document=document,
        segments=[TranscriptSegmentSnapshot(segment=filler)],
        recognized_word_count=0,
        estimated_word_count=0,
    )

    review = review_transcript(
        snapshot,
        SpeechReviewRules(review_pauses=False, review_edge_gaps=False),
        frame_profile=ProjectProfile(fps_numerator=25, fps_denominator=1),
        timeline_duration_seconds=0.2,
    )

    assert review.filler_candidate_count == 1
    candidate = review.candidates[0]
    assert candidate.kind == "filler"
    assert candidate.selection_kind == "segments"
    assert candidate.selection_ids == ["filler-segment"]
    assert candidate.timing_precision == "segment_only"
