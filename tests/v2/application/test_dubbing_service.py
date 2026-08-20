import pytest

from mediaflow.application.dubbing_service import DubbingPreparationService
from mediaflow.domain.dubbing import DiarizationResult, DiarizationTurn, DubbingSettings
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.subtitles import SubtitleSegment, SubtitleWord


def test_dubbing_preparation_maps_speakers_merges_lines_and_selects_references() -> None:
    profile = ProjectProfile(fps_numerator=30, fps_denominator=1)
    source = [
        SubtitleSegment(
            id="source-a1",
            document_id="source",
            start_frame=0,
            end_frame=75,
            text="Hello there.",
        ),
        SubtitleSegment(
            id="source-a2",
            document_id="source",
            start_frame=78,
            end_frame=180,
            text="How are you today?",
        ),
        SubtitleSegment(
            id="source-b1",
            document_id="source",
            start_frame=210,
            end_frame=330,
            text="I am doing well.",
        ),
    ]
    target = [
        SubtitleSegment(
            id="target-a1",
            document_id="target",
            source_segment_id="source-a1",
            start_frame=0,
            end_frame=75,
            text="你好。",
        ),
        SubtitleSegment(
            id="target-a2",
            document_id="target",
            source_segment_id="source-a2",
            start_frame=78,
            end_frame=180,
            text="你今天怎么样？",
        ),
        SubtitleSegment(
            id="target-b1",
            document_id="target",
            source_segment_id="source-b1",
            start_frame=210,
            end_frame=330,
            text="我很好。",
        ),
    ]
    diarization = DiarizationResult(
        engine="pyannote.audio",
        engine_version="4.0",
        model="community-1",
        device="cpu",
        exclusive=True,
        turns=(
            DiarizationTurn(speaker="SPEAKER_00", start_seconds=0, end_seconds=6.1),
            DiarizationTurn(speaker="SPEAKER_01", start_seconds=7, end_seconds=11),
        ),
    )

    plan = DubbingPreparationService().prepare(
        source_segments=source,
        target_segments=target,
        diarization=diarization,
        main_profile=profile,
        sequence_profile=profile,
        settings=DubbingSettings(merge_gap_frames=6),
    )

    assert [speaker.id for speaker in plan.speakers] == ["speaker-01", "speaker-02"]
    assert len(plan.utterances) == 2
    assert plan.utterances[0].source_segment_ids == ["source-a1", "source-a2"]
    assert plan.utterances[0].speaker_id == "speaker-01"
    assert plan.utterances[0].target_text == "你好。你今天怎么样？"
    assert plan.utterances[1].speaker_id == "speaker-02"
    assert plan.reference_candidates["speaker-01"][0].text == (
        "Hello there. How are you today?"
    )
    assert plan.reference_candidates["speaker-02"][0].start_frame == 210


def test_long_source_line_uses_exact_word_window_within_gpt_reference_limit() -> None:
    profile = ProjectProfile(fps_numerator=30, fps_denominator=1)
    source = SubtitleSegment(
        id="source-long",
        document_id="source",
        start_frame=0,
        end_frame=360,
        text="one two three four five six seven eight nine ten eleven twelve",
    )
    target = SubtitleSegment(
        id="target-long",
        document_id="target",
        source_segment_id=source.id,
        start_frame=0,
        end_frame=360,
        text="这是一条很长的译文。",
    )
    words = [
        SubtitleWord(
            segment_id=source.id,
            position=index,
            start_frame=index * 30,
            end_frame=(index + 1) * 30,
            text=text,
        )
        for index, text in enumerate(source.text.split())
    ]

    plan = DubbingPreparationService().prepare(
        source_segments=[source],
        target_segments=[target],
        source_words=words,
        diarization=DiarizationResult(
            engine="pyannote.audio",
            engine_version="4.0.7",
            model="community-1",
            device="cuda",
            exclusive=True,
            turns=(
                DiarizationTurn(
                    speaker="SPEAKER_00",
                    start_seconds=0.0,
                    end_seconds=12.0,
                ),
            ),
        ),
        main_profile=profile,
        sequence_profile=profile,
        settings=DubbingSettings(),
    )

    candidate = plan.reference_candidates["speaker-01"][0]
    duration = (candidate.end_frame - candidate.start_frame) / 30
    assert 3.0 <= duration <= 9.8
    assert candidate.transcript_exact is True
    assert candidate.text in source.text
    assert plan.speakers[0].review_status == "automatic"


def test_long_source_without_words_extracts_safe_window_and_requires_review() -> None:
    profile = ProjectProfile(fps_numerator=30, fps_denominator=1)
    source = SubtitleSegment(
        id="source-long",
        document_id="source",
        start_frame=0,
        end_frame=360,
        text="A long imported subtitle without word timestamps.",
    )
    target = SubtitleSegment(
        id="target-long",
        document_id="target",
        source_segment_id=source.id,
        start_frame=0,
        end_frame=360,
        text="没有词级时间的长字幕。",
    )

    plan = DubbingPreparationService().prepare(
        source_segments=[source],
        target_segments=[target],
        diarization=DiarizationResult(
            engine="pyannote.audio",
            engine_version="4.0.7",
            model="community-1",
            device="cuda",
            exclusive=True,
            turns=(
                DiarizationTurn(
                    speaker="SPEAKER_00",
                    start_seconds=0.0,
                    end_seconds=12.0,
                ),
            ),
        ),
        main_profile=profile,
        sequence_profile=profile,
        settings=DubbingSettings(),
    )

    candidate = plan.reference_candidates["speaker-01"][0]
    assert (candidate.end_frame - candidate.start_frame) / 30 <= 9.8
    assert candidate.transcript_exact is False
    assert plan.speakers[0].review_status == "needs_review"


def test_gpt_reference_settings_reject_durations_outside_supported_range() -> None:
    with pytest.raises(ValueError):
        DubbingSettings(reference_min_seconds=2.9)
    with pytest.raises(ValueError):
        DubbingSettings(reference_max_seconds=10.0)


def test_dubbing_preparation_marks_weak_speaker_overlap_for_review() -> None:
    profile = ProjectProfile(fps_numerator=30, fps_denominator=1)
    source = SubtitleSegment(
        id="source",
        document_id="source-document",
        start_frame=0,
        end_frame=90,
        text="A long subtitle with little detected speech.",
    )
    target = SubtitleSegment(
        id="target",
        document_id="target-document",
        source_segment_id=source.id,
        start_frame=0,
        end_frame=90,
        text="一条很长但只有少量语音覆盖的字幕。",
    )
    result = DubbingPreparationService().prepare(
        source_segments=[source],
        target_segments=[target],
        diarization=DiarizationResult(
            engine="pyannote.audio",
            engine_version="4.0",
            model="community-1",
            device="cpu",
            exclusive=True,
            turns=(
                DiarizationTurn(
                    speaker="SPEAKER_00",
                    start_seconds=0,
                    end_seconds=0.5,
                ),
            ),
        ),
        main_profile=profile,
        sequence_profile=profile,
        settings=DubbingSettings(),
    )

    assert result.utterances[0].review_status == "needs_review"
    assert "覆盖率" in result.utterances[0].issues[0]


def test_dubbing_preparation_rejects_overlapping_dialogue() -> None:
    profile = ProjectProfile()
    sources = [
        SubtitleSegment(
            id="source-1",
            document_id="source",
            start_frame=0,
            end_frame=60,
            text="First",
        ),
        SubtitleSegment(
            id="source-2",
            document_id="source",
            start_frame=50,
            end_frame=100,
            text="Second",
        ),
    ]
    targets = [
        SubtitleSegment(
            id=f"target-{index}",
            document_id="target",
            source_segment_id=source.id,
            start_frame=source.start_frame,
            end_frame=source.end_frame,
            text=f"译文 {index}",
        )
        for index, source in enumerate(sources, start=1)
    ]
    diarization = DiarizationResult(
        engine="pyannote.audio",
        engine_version="4.0.7",
        model="community-1",
        device="cpu",
        exclusive=True,
        turns=(
            DiarizationTurn(
                speaker="SPEAKER_00",
                start_seconds=0.0,
                end_seconds=3.5,
            ),
        ),
    )

    with pytest.raises(ValueError, match="只支持没有重叠"):
        DubbingPreparationService().prepare(
            source_segments=sources,
            target_segments=targets,
            diarization=diarization,
            main_profile=profile,
            sequence_profile=profile,
            settings=DubbingSettings(),
        )
