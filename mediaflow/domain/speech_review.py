from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .model_base import DomainModel
from .project import ProjectProfile
from .transcript_edits import TranscriptSnapshot


class SpeechReviewSegment(DomainModel):
    id: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def positive_duration(self) -> SpeechReviewSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Speech review segment end must be after its start")
        return self


class SpeechReviewRules(DomainModel):
    threshold: float = Field(default=0.82, ge=0.5, le=1)
    max_gap_seconds: float = Field(default=12, ge=0, le=120)
    min_characters: int = Field(default=6, ge=2, le=100)
    max_candidates: int = Field(default=50, ge=1, le=500)
    block_on_candidates: bool = True
    review_fillers: bool = True
    filler_terms: list[str] = Field(
        default_factory=lambda: ["嗯", "呃", "额", "啊", "唔", "um", "uh", "erm", "hmm"]
    )
    review_pauses: bool = True
    pause_threshold_seconds: float = Field(default=0.8, ge=0.1, le=30)
    review_edge_gaps: bool = True
    leading_gap_threshold_seconds: float = Field(default=0.12, ge=0, le=10)
    trailing_gap_threshold_seconds: float = Field(default=0.4, ge=0, le=30)

    @field_validator("filler_terms")
    @classmethod
    def normalize_filler_terms(cls, values: list[str]) -> list[str]:
        normalized = list(
            dict.fromkeys(value for item in values if (value := _normalized_speech(item)))
        )
        if not normalized:
            raise ValueError("filler_terms must contain at least one visible term")
        return normalized


class SpeechSimilarityCandidate(DomainModel):
    kind: Literal["exact_duplicate", "restart_extension", "near_duplicate"]
    first_segment_id: str
    second_segment_id: str
    first_start_seconds: float
    first_end_seconds: float
    second_start_seconds: float
    second_end_seconds: float
    first_text: str
    second_text: str
    gap_seconds: float = Field(ge=0)
    similarity: float = Field(ge=0, le=1)
    manual_review_required: Literal[True] = True
    suggested_action: Literal["transcript.edit.preview"] = "transcript.edit.preview"


class SpeechFillerCandidate(DomainModel):
    kind: Literal["filler"] = "filler"
    segment_id: str
    selection_kind: Literal["words", "segments"]
    selection_ids: list[str] = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1)
    timing_precision: Literal[
        "recognized_words",
        "estimated_words",
        "mixed_words",
        "segment_only",
    ]
    manual_review_required: Literal[True] = True
    suggested_action: Literal["transcript.edit.preview"] = "transcript.edit.preview"


class SpeechGapCandidate(DomainModel):
    kind: Literal["leading_gap", "pause", "trailing_gap"]
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    previous_segment_id: str | None = None
    next_segment_id: str | None = None
    evidence: str = Field(min_length=1)
    manual_review_required: Literal[True] = True
    suggested_action: Literal["script.gap.close", "listen"]


SpeechReviewCandidate = Annotated[
    SpeechSimilarityCandidate | SpeechFillerCandidate | SpeechGapCandidate,
    Field(discriminator="kind"),
]


class SpeechReviewResult(DomainModel):
    requested: bool
    threshold: float | None = None
    candidate_count: int = Field(ge=0)
    similarity_candidate_count: int = Field(default=0, ge=0)
    filler_candidate_count: int = Field(default=0, ge=0)
    gap_candidate_count: int = Field(default=0, ge=0)
    candidates: list[SpeechReviewCandidate]
    blocks_production: bool
    automatic_deletions: Literal[0] = 0


class ProjectSpeechReviewInspection(DomainModel):
    sequence_id: str
    document_id: str
    content_revision: int = Field(ge=0)
    timeline_duration_seconds: float = Field(ge=0)
    recognized_word_count: int = Field(ge=0)
    estimated_word_count: int = Field(ge=0)
    review: SpeechReviewResult


def _normalized_speech(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith(("P", "S", "Z", "C"))
    )


def _similarity(
    first: str,
    second: str,
) -> tuple[float, Literal["exact_duplicate", "restart_extension", "near_duplicate"]]:
    if first == second:
        return 1.0, "exact_duplicate"
    sequence_ratio = SequenceMatcher(None, first, second, autojunk=False).ratio()
    shorter, longer = sorted((first, second), key=len)
    length_ratio = len(shorter) / len(longer)
    prefix_length = 0
    for left, right in zip(first, second, strict=False):
        if left != right:
            break
        prefix_length += 1
    prefix_coverage = prefix_length / len(shorter)
    contained = shorter in longer
    restart_score = min(1.0, length_ratio + 0.2) if contained else 0.0
    if prefix_coverage >= 0.7:
        restart_score = max(restart_score, 0.9 * prefix_coverage + 0.1 * length_ratio)
    score = max(sequence_ratio, restart_score)
    kind: Literal["restart_extension", "near_duplicate"] = (
        "restart_extension" if restart_score >= sequence_ratio else "near_duplicate"
    )
    return score, kind


def _similarity_candidates(
    segments: list[SpeechReviewSegment],
    rules: SpeechReviewRules,
) -> list[SpeechSimilarityCandidate]:
    ordered = sorted(segments, key=lambda item: (item.start_seconds, item.id))
    normalized = {segment.id: _normalized_speech(segment.text) for segment in ordered}
    candidates: list[SpeechSimilarityCandidate] = []
    for index, first in enumerate(ordered):
        first_text = normalized[first.id]
        if len(first_text) < rules.min_characters:
            continue
        for second in ordered[index + 1 :]:
            gap = max(0.0, second.start_seconds - first.end_seconds)
            if gap > rules.max_gap_seconds:
                break
            second_text = normalized[second.id]
            if len(second_text) < rules.min_characters:
                continue
            score, kind = _similarity(first_text, second_text)
            if score + 1e-12 < rules.threshold:
                continue
            candidates.append(
                SpeechSimilarityCandidate(
                    kind=kind,
                    first_segment_id=first.id,
                    second_segment_id=second.id,
                    first_start_seconds=first.start_seconds,
                    first_end_seconds=first.end_seconds,
                    second_start_seconds=second.start_seconds,
                    second_end_seconds=second.end_seconds,
                    first_text=first.text,
                    second_text=second.text,
                    gap_seconds=round(gap, 4),
                    similarity=round(score, 4),
                )
            )
    return candidates


def _candidate_start(candidate: SpeechReviewCandidate) -> float:
    if isinstance(candidate, SpeechSimilarityCandidate):
        return candidate.first_start_seconds
    return candidate.start_seconds


def _result(
    candidates: list[SpeechReviewCandidate],
    rules: SpeechReviewRules,
) -> SpeechReviewResult:
    candidates = sorted(candidates, key=lambda item: (_candidate_start(item), item.kind))[
        : rules.max_candidates
    ]
    return SpeechReviewResult(
        requested=True,
        threshold=rules.threshold,
        candidate_count=len(candidates),
        similarity_candidate_count=sum(
            isinstance(item, SpeechSimilarityCandidate) for item in candidates
        ),
        filler_candidate_count=sum(isinstance(item, SpeechFillerCandidate) for item in candidates),
        gap_candidate_count=sum(isinstance(item, SpeechGapCandidate) for item in candidates),
        candidates=candidates,
        blocks_production=bool(candidates) and rules.block_on_candidates,
    )


def review_speech_segments(
    segments: list[SpeechReviewSegment] | None,
    rules: SpeechReviewRules,
) -> SpeechReviewResult:
    if segments is None:
        return SpeechReviewResult(
            requested=False,
            candidate_count=0,
            candidates=[],
            blocks_production=False,
        )
    return _result(list(_similarity_candidates(segments, rules)), rules)


def review_transcript(
    snapshot: TranscriptSnapshot,
    rules: SpeechReviewRules,
    *,
    frame_profile: ProjectProfile,
    timeline_duration_seconds: float,
) -> SpeechReviewResult:
    fps = frame_profile.fps_numerator / frame_profile.fps_denominator
    ordered = sorted(
        snapshot.segments,
        key=lambda item: (item.segment.start_frame, item.segment.id),
    )
    segments = [
        SpeechReviewSegment(
            id=item.segment.id,
            start_seconds=item.segment.start_frame / fps,
            end_seconds=item.segment.end_frame / fps,
            text=item.segment.text,
        )
        for item in ordered
    ]
    candidates: list[SpeechReviewCandidate] = list(_similarity_candidates(segments, rules))

    filler_terms = set(rules.filler_terms)
    single_character_fillers = {term for term in filler_terms if len(term) == 1}
    if rules.review_fillers:
        for item in ordered:
            matched_word = False
            for word in item.words:
                if _normalized_speech(word.text) not in filler_terms:
                    continue
                matched_word = True
                precision: Literal["recognized_words", "estimated_words"] = (
                    "recognized_words" if word.timing_source == "recognized" else "estimated_words"
                )
                candidates.append(
                    SpeechFillerCandidate(
                        segment_id=item.segment.id,
                        selection_kind="words",
                        selection_ids=[word.id],
                        start_seconds=word.start_frame / fps,
                        end_seconds=word.end_frame / fps,
                        text=word.text,
                        timing_precision=precision,
                    )
                )
            segment_text = _normalized_speech(item.segment.text)
            segment_is_only_fillers = bool(segment_text) and (
                segment_text in filler_terms
                or all(character in single_character_fillers for character in segment_text)
            )
            if not matched_word and segment_is_only_fillers:
                candidates.append(
                    SpeechFillerCandidate(
                        segment_id=item.segment.id,
                        selection_kind="segments",
                        selection_ids=[item.segment.id],
                        start_seconds=item.segment.start_frame / fps,
                        end_seconds=item.segment.end_frame / fps,
                        text=item.segment.text,
                        timing_precision="segment_only",
                    )
                )

    if ordered:
        first_start = ordered[0].segment.start_frame / fps
        if rules.review_edge_gaps and first_start >= rules.leading_gap_threshold_seconds > 0:
            candidates.append(
                SpeechGapCandidate(
                    kind="leading_gap",
                    start_seconds=0,
                    end_seconds=first_start,
                    duration_seconds=round(first_start, 4),
                    next_segment_id=ordered[0].segment.id,
                    evidence=(
                        "No recognized speech appears before the first segment. The region may contain "
                        "an inhale, mouth noise, room tone, or waiting; listen before closing it."
                    ),
                    suggested_action="script.gap.close",
                )
            )
        if rules.review_pauses:
            for previous, following in zip(ordered, ordered[1:], strict=False):
                gap_start = previous.segment.end_frame / fps
                gap_end = following.segment.start_frame / fps
                duration = gap_end - gap_start
                if duration < rules.pause_threshold_seconds:
                    continue
                candidates.append(
                    SpeechGapCandidate(
                        kind="pause",
                        start_seconds=gap_start,
                        end_seconds=gap_end,
                        duration_seconds=round(duration, 4),
                        previous_segment_id=previous.segment.id,
                        next_segment_id=following.segment.id,
                        evidence="No recognized speech appears between adjacent transcript segments.",
                        suggested_action="script.gap.close",
                    )
                )
        final_end = ordered[-1].segment.end_frame / fps
        trailing = timeline_duration_seconds - final_end
        if rules.review_edge_gaps and trailing >= rules.trailing_gap_threshold_seconds > 0:
            candidates.append(
                SpeechGapCandidate(
                    kind="trailing_gap",
                    start_seconds=final_end,
                    end_seconds=timeline_duration_seconds,
                    duration_seconds=round(trailing, 4),
                    previous_segment_id=ordered[-1].segment.id,
                    evidence="No recognized speech appears after the final transcript segment.",
                    suggested_action="listen",
                )
            )
    return _result(candidates, rules)
