from __future__ import annotations

import re
from dataclasses import dataclass

from mediaflow.domain.dubbing import (
    DiarizationResult,
    DubbingSettings,
    DubbingSpeaker,
    DubbingSpeakerTurn,
    DubbingUtterance,
)
from mediaflow.domain.model_base import new_id
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.subtitles import SubtitleSegment, SubtitleWord
from mediaflow.domain.timebase import reframe_interval, seconds_to_frames


@dataclass(frozen=True, slots=True)
class DubbingReferenceCandidate:
    speaker_id: str
    start_frame: int
    end_frame: int
    text: str
    source_segment_ids: tuple[str, ...]
    score: float
    transcript_exact: bool = True


@dataclass(frozen=True, slots=True)
class DubbingPreparationPlan:
    speakers: tuple[DubbingSpeaker, ...]
    turns: tuple[DubbingSpeakerTurn, ...]
    utterances: tuple[DubbingUtterance, ...]
    reference_candidates: dict[str, tuple[DubbingReferenceCandidate, ...]]


@dataclass(frozen=True, slots=True)
class _TimedSource:
    segment: SubtitleSegment
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True)
class _TimedWord:
    word: SubtitleWord
    start_frame: int
    end_frame: int


class DubbingPreparationService:
    def prepare(
        self,
        *,
        source_segments: list[SubtitleSegment],
        target_segments: list[SubtitleSegment],
        diarization: DiarizationResult,
        main_profile: ProjectProfile,
        sequence_profile: ProjectProfile,
        settings: DubbingSettings,
        source_words: list[SubtitleWord] | None = None,
    ) -> DubbingPreparationPlan:
        if not source_segments:
            raise ValueError("源字幕文档为空")
        if not target_segments:
            raise ValueError("目标语言字幕文档为空")
        labels = list(
            dict.fromkeys(
                turn.speaker
                for turn in sorted(
                    diarization.turns,
                    key=lambda item: (item.start_seconds, item.end_seconds),
                )
            )
        )
        speaker_ids = {
            label: f"speaker-{index:02d}"
            for index, label in enumerate(labels, start=1)
        }
        turns = tuple(
            DubbingSpeakerTurn(
                speaker_id=speaker_ids[item.speaker],
                start_frame=max(
                    0,
                    seconds_to_frames(
                        item.start_seconds,
                        sequence_profile.fps_numerator,
                        sequence_profile.fps_denominator,
                    ),
                ),
                end_frame=max(
                    1,
                    seconds_to_frames(
                        item.end_seconds,
                        sequence_profile.fps_numerator,
                        sequence_profile.fps_denominator,
                    ),
                ),
            )
            for item in diarization.turns
        )
        timed_sources = [
            _TimedSource(
                segment=segment,
                start_frame=reframe_interval(
                    segment.start_frame,
                    segment.end_frame,
                    main_profile,
                    sequence_profile,
                )[0],
                end_frame=reframe_interval(
                    segment.start_frame,
                    segment.end_frame,
                    main_profile,
                    sequence_profile,
                )[1],
            )
            for segment in sorted(
                source_segments,
                key=lambda item: (item.start_frame, item.end_frame, item.id),
            )
        ]
        source_segment_ids = {item.segment.id for item in timed_sources}
        timed_words = tuple(
            _TimedWord(
                word=word,
                start_frame=reframe_interval(
                    word.start_frame,
                    word.end_frame,
                    main_profile,
                    sequence_profile,
                )[0],
                end_frame=reframe_interval(
                    word.start_frame,
                    word.end_frame,
                    main_profile,
                    sequence_profile,
                )[1],
            )
            for word in (source_words or ())
            if not word.excluded and word.segment_id in source_segment_ids
        )
        if any(
            left.end_frame > right.start_frame
            for left, right in zip(
                timed_sources,
                timed_sources[1:],
                strict=False,
            )
        ):
            raise ValueError("当前多人配音只支持没有重叠讲话的对白")
        target_by_source = {
            item.source_segment_id: item
            for item in target_segments
            if item.source_segment_id is not None
        }
        atomic: list[DubbingUtterance] = []
        for source in timed_sources:
            speaker_id, coverage = self._speaker_for_range(
                source.start_frame,
                source.end_frame,
                turns,
            )
            target = target_by_source.get(source.segment.id)
            if target is None:
                overlapping = [
                    item
                    for item in target_segments
                    if item.end_frame > source.segment.start_frame
                    and item.start_frame < source.segment.end_frame
                ]
                if len(overlapping) != 1:
                    raise RuntimeError(
                        "译文无法逐句对应源字幕，请使用标准翻译模式后再创建配音方案"
                    )
                target = overlapping[0]
            issues: list[str] = []
            if coverage < 0.6:
                issues.append("说话人覆盖率较低，请人工确认")
            atomic.append(
                DubbingUtterance(
                    speaker_id=speaker_id,
                    source_segment_ids=[source.segment.id],
                    target_segment_ids=[target.id],
                    start_frame=source.start_frame,
                    end_frame=source.end_frame,
                    source_text=source.segment.text,
                    target_text=target.text,
                    review_status="needs_review" if issues else "automatic",
                    issues=issues,
                )
            )
        utterances = tuple(self._merge_utterances(atomic, settings.merge_gap_frames))
        candidates = {
            speaker_id: tuple(
                self._reference_candidates(
                    speaker_id,
                    utterances,
                    timed_words=timed_words,
                    settings=settings,
                    sequence_profile=sequence_profile,
                )
            )
            for speaker_id in speaker_ids.values()
        }
        speakers = tuple(
            DubbingSpeaker(
                id=speaker_ids[label],
                label=label,
                display_name=f"说话人 {index}",
                review_status=(
                    "automatic"
                    if candidates[speaker_ids[label]]
                    and candidates[speaker_ids[label]][0].transcript_exact
                    and self._frames_to_seconds(
                        candidates[speaker_ids[label]][0].end_frame
                        - candidates[speaker_ids[label]][0].start_frame,
                        sequence_profile,
                    )
                    >= settings.reference_min_seconds
                    else "needs_review"
                ),
            )
            for index, label in enumerate(labels, start=1)
        )
        return DubbingPreparationPlan(
            speakers=speakers,
            turns=turns,
            utterances=utterances,
            reference_candidates=candidates,
        )

    @staticmethod
    def _speaker_for_range(
        start_frame: int,
        end_frame: int,
        turns: tuple[DubbingSpeakerTurn, ...],
    ) -> tuple[str, float]:
        overlap: dict[str, int] = {}
        for turn in turns:
            frames = max(
                0,
                min(end_frame, turn.end_frame) - max(start_frame, turn.start_frame),
            )
            if frames:
                overlap[turn.speaker_id] = overlap.get(turn.speaker_id, 0) + frames
        if overlap:
            speaker_id, frames = max(overlap.items(), key=lambda item: (item[1], item[0]))
            return speaker_id, frames / (end_frame - start_frame)
        midpoint = (start_frame + end_frame) / 2
        nearest = min(
            turns,
            key=lambda item: min(
                abs(midpoint - item.start_frame),
                abs(midpoint - item.end_frame),
            ),
        )
        return nearest.speaker_id, 0.0

    @staticmethod
    def _merge_utterances(
        values: list[DubbingUtterance],
        merge_gap_frames: int,
    ) -> list[DubbingUtterance]:
        merged: list[DubbingUtterance] = []
        for item in values:
            if (
                merged
                and merged[-1].speaker_id == item.speaker_id
                and item.start_frame - merged[-1].end_frame <= merge_gap_frames
            ):
                previous = merged[-1]
                issues = list(dict.fromkeys([*previous.issues, *item.issues]))
                merged[-1] = previous.model_copy(
                    update={
                        "source_segment_ids": [
                            *previous.source_segment_ids,
                            *item.source_segment_ids,
                        ],
                        "target_segment_ids": [
                            *previous.target_segment_ids,
                            *item.target_segment_ids,
                        ],
                        "end_frame": item.end_frame,
                        "source_text": f"{previous.source_text} {item.source_text}",
                        "target_text": f"{previous.target_text}{item.target_text}",
                        "review_status": "needs_review" if issues else "automatic",
                        "issues": issues,
                    }
                )
            else:
                merged.append(item.model_copy(update={"id": new_id()}))
        return merged

    def _reference_candidates(
        self,
        speaker_id: str,
        utterances: tuple[DubbingUtterance, ...],
        *,
        timed_words: tuple[_TimedWord, ...],
        settings: DubbingSettings,
        sequence_profile: ProjectProfile,
    ) -> list[DubbingReferenceCandidate]:
        indices = [
            index
            for index, item in enumerate(utterances)
            if item.speaker_id == speaker_id
        ]
        candidates: list[DubbingReferenceCandidate] = []
        words_by_segment: dict[str, list[_TimedWord]] = {}
        for timed_word in timed_words:
            words_by_segment.setdefault(
                timed_word.word.segment_id,
                [],
            ).append(timed_word)
        for utterance in (item for item in utterances if item.speaker_id == speaker_id):
            words = sorted(
                (
                    word
                    for segment_id in utterance.source_segment_ids
                    for word in words_by_segment.get(segment_id, ())
                    if word.end_frame > utterance.start_frame
                    and word.start_frame < utterance.end_frame
                ),
                key=lambda item: (
                    item.start_frame,
                    item.end_frame,
                    item.word.position,
                    item.word.id,
                ),
            )
            candidates.extend(
                self._word_reference_candidates(
                    speaker_id,
                    words,
                    settings=settings,
                    sequence_profile=sequence_profile,
                )
            )
        maximum_gap = max(
            settings.merge_gap_frames,
            seconds_to_frames(
                0.75,
                sequence_profile.fps_numerator,
                sequence_profile.fps_denominator,
            ),
        )
        for source_index in indices:
            selected_utterances = [utterances[source_index]]
            end_frame = selected_utterances[0].end_frame
            for next_index in indices:
                if next_index <= source_index:
                    continue
                next_utterance = utterances[next_index]
                if next_utterance.start_frame - end_frame > maximum_gap:
                    break
                candidate_seconds = self._frames_to_seconds(
                    next_utterance.end_frame - selected_utterances[0].start_frame,
                    sequence_profile,
                )
                if candidate_seconds > settings.reference_max_seconds:
                    break
                selected_utterances.append(next_utterance)
                end_frame = next_utterance.end_frame
                if candidate_seconds >= settings.reference_min_seconds:
                    break
            start_frame = selected_utterances[0].start_frame
            duration = self._frames_to_seconds(end_frame - start_frame, sequence_profile)
            if (
                duration < settings.reference_min_seconds
                or duration > settings.reference_max_seconds
            ):
                continue
            issues = sum(bool(item.issues) for item in selected_utterances)
            score = -abs(duration - 6.0) - issues * 3.0
            candidates.append(
                DubbingReferenceCandidate(
                    speaker_id=speaker_id,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    text=" ".join(
                        utterance.source_text
                        for utterance in selected_utterances
                    ),
                    source_segment_ids=tuple(
                        segment_id
                        for utterance in selected_utterances
                        for segment_id in utterance.source_segment_ids
                    ),
                    score=score,
                )
            )
        if not candidates:
            maximum_frames = max(
                1,
                int(
                    settings.reference_max_seconds
                    * sequence_profile.fps_numerator
                    / sequence_profile.fps_denominator
                ),
            )
            for utterance in (
                item for item in utterances if item.speaker_id == speaker_id
            ):
                end_frame = min(
                    utterance.end_frame,
                    utterance.start_frame + maximum_frames,
                )
                duration = self._frames_to_seconds(
                    end_frame - utterance.start_frame,
                    sequence_profile,
                )
                if duration < settings.reference_min_seconds:
                    continue
                candidates.append(
                    DubbingReferenceCandidate(
                        speaker_id=speaker_id,
                        start_frame=utterance.start_frame,
                        end_frame=end_frame,
                        text=utterance.source_text,
                        source_segment_ids=tuple(utterance.source_segment_ids),
                        score=-abs(duration - 6.0) - 6.0,
                        transcript_exact=end_frame == utterance.end_frame,
                    )
                )
        unique: dict[tuple[int, int], DubbingReferenceCandidate] = {}
        for candidate in sorted(candidates, key=lambda value: value.score, reverse=True):
            unique.setdefault(
                (candidate.start_frame, candidate.end_frame),
                candidate,
            )
        chosen: list[DubbingReferenceCandidate] = []
        for candidate in unique.values():
            if any(
                self._range_overlap_ratio(candidate, existing) >= 0.8
                for existing in chosen
            ):
                continue
            chosen.append(candidate)
            if len(chosen) == 3:
                break
        return chosen

    def _word_reference_candidates(
        self,
        speaker_id: str,
        words: list[_TimedWord],
        *,
        settings: DubbingSettings,
        sequence_profile: ProjectProfile,
    ) -> list[DubbingReferenceCandidate]:
        candidates: list[DubbingReferenceCandidate] = []
        for start_index, first in enumerate(words):
            best: DubbingReferenceCandidate | None = None
            for end_index in range(start_index, len(words)):
                last = words[end_index]
                duration = self._frames_to_seconds(
                    last.end_frame - first.start_frame,
                    sequence_profile,
                )
                if duration > settings.reference_max_seconds:
                    break
                if duration < settings.reference_min_seconds:
                    continue
                selected = words[start_index : end_index + 1]
                segment_ids = tuple(
                    dict.fromkeys(item.word.segment_id for item in selected)
                )
                timing_penalty = 0.5 * sum(
                    item.word.timing_source == "estimated" for item in selected
                ) / len(selected)
                candidate = DubbingReferenceCandidate(
                    speaker_id=speaker_id,
                    start_frame=first.start_frame,
                    end_frame=last.end_frame,
                    text=self._join_words(selected),
                    source_segment_ids=segment_ids,
                    score=-abs(duration - 6.0) - timing_penalty,
                )
                if best is None or candidate.score > best.score:
                    best = candidate
            if best is not None:
                candidates.append(best)
        return candidates

    @staticmethod
    def _join_words(words: list[_TimedWord]) -> str:
        values = [item.word.text.strip() for item in words if item.word.text.strip()]
        if any(re.search(r"[\u3400-\u9fff]", value) for value in values):
            return "".join(values)
        return re.sub(r"\s+([,.;:!?%])", r"\1", " ".join(values)).strip()

    @staticmethod
    def _range_overlap_ratio(
        left: DubbingReferenceCandidate,
        right: DubbingReferenceCandidate,
    ) -> float:
        overlap = max(
            0,
            min(left.end_frame, right.end_frame)
            - max(left.start_frame, right.start_frame),
        )
        shorter = min(
            left.end_frame - left.start_frame,
            right.end_frame - right.start_frame,
        )
        return overlap / shorter if shorter else 0.0

    @staticmethod
    def _frames_to_seconds(frames: int, profile: ProjectProfile) -> float:
        return frames * profile.fps_denominator / profile.fps_numerator
