from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Literal

from mediaflow.application.edit_history import ProjectEditCommand, ProjectEditHistory
from mediaflow.application.ports import TranscriptEditingDocuments
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.domain.timebase import reframe_interval
from mediaflow.domain.transcript_edits import (
    TranscriptEditImpact,
    TranscriptEditPlan,
    TranscriptEditRequest,
    TranscriptEditResult,
    TranscriptFrameInterval,
    TranscriptResolvedSelection,
    TranscriptSegmentSnapshot,
    TranscriptSnapshot,
)


class TranscriptEditingService:
    """Preview and apply revision-bound transcript edit plans for automation clients."""

    def __init__(
        self,
        repository: TranscriptEditingDocuments,
        publication: SubtitlePublicationService,
        history: ProjectEditHistory,
    ):
        self.repository = repository
        self.publication = publication
        self.history = history

    def inspect_transcript(
        self,
        sequence_id: str,
        *,
        document_id: str | None = None,
    ) -> TranscriptSnapshot:
        document = self._source_transcript(sequence_id, document_id)
        segments = self.repository.subtitles.list_subtitle_segments(document.id)
        words = self.repository.subtitles.list_subtitle_words(
            document.id,
            include_excluded=False,
        )
        words_by_segment: dict[str, list[SubtitleWord]] = {}
        for word in words:
            words_by_segment.setdefault(word.segment_id, []).append(word)
        return TranscriptSnapshot(
            content_revision=self.repository.content_revision(),
            document=document,
            segments=[
                TranscriptSegmentSnapshot(
                    segment=segment,
                    words=sorted(
                        words_by_segment.get(segment.id, []),
                        key=lambda item: (item.position, item.start_frame, item.id),
                    ),
                )
                for segment in segments
            ],
            recognized_word_count=sum(
                word.timing_source == "recognized" for word in words
            ),
            estimated_word_count=sum(word.timing_source == "estimated" for word in words),
        )

    def preview_plan(
        self,
        request: TranscriptEditRequest,
        timeline: TimelineEditor,
    ) -> TranscriptEditPlan:
        self._require_content_revision(request.expected_content_revision)
        document = self._source_transcript(request.sequence_id, request.document_id)
        if timeline.sequence_id != request.sequence_id:
            raise ValueError("转录剪辑计划与当前时间轴不一致")
        project = self.repository.catalog.get_project()
        main_profile = self.repository.catalog.get_sequence(
            project.main_sequence_id
        ).profile
        sequence_profile = timeline.state.sequence.profile
        segments = self.repository.subtitles.list_subtitle_segments(document.id)
        words = self.repository.subtitles.list_subtitle_words(document.id)
        segments_by_id = {segment.id: segment for segment in segments}
        words_by_id = {word.id: word for word in words}
        selected_segment_ids = {
            item_id
            for selection in request.selections
            if selection.kind == "segments"
            for item_id in selection.ids
        }
        selected_word_ids = {
            item_id
            for selection in request.selections
            if selection.kind == "words"
            for item_id in selection.ids
        }
        self._validate_targets(
            selected_segment_ids,
            selected_word_ids,
            segments_by_id,
            words_by_id,
        )

        resolved: list[TranscriptResolvedSelection] = []
        raw_intervals: list[tuple[int, int]] = []
        for selection in request.selections:
            if selection.kind == "words":
                selected_words = sorted(
                    (words_by_id[item_id] for item_id in selection.ids),
                    key=lambda item: (item.start_frame, item.end_frame, item.id),
                )
                selection_intervals = self._merged_intervals(
                    (word.start_frame, word.end_frame) for word in selected_words
                )
                text = self._join_words(selected_words)
                timing: Literal["recognized_words", "subtitle_segments"] = (
                    "recognized_words"
                )
            else:
                selected_segments = sorted(
                    (segments_by_id[item_id] for item_id in selection.ids),
                    key=lambda item: (item.start_frame, item.end_frame, item.id),
                )
                selection_intervals = self._merged_intervals(
                    (segment.start_frame, segment.end_frame)
                    for segment in selected_segments
                )
                text = "\n".join(segment.text.strip() for segment in selected_segments)
                timing = "subtitle_segments"
            raw_intervals.extend(selection_intervals)
            resolved.append(
                TranscriptResolvedSelection(
                    kind=selection.kind,
                    ids=selection.ids,
                    reason=selection.reason,
                    text=text,
                    intervals=[
                        TranscriptFrameInterval(start_frame=start, end_frame=end)
                        for start, end in selection_intervals
                    ],
                    timing=timing,
                )
            )

        subtitle_intervals = self._merged_intervals(raw_intervals)
        timeline_intervals = self._merged_intervals(
            reframe_interval(
                start,
                end,
                main_profile,
                sequence_profile,
            )
            for start, end in subtitle_intervals
        )
        before_state = timeline.state
        after_state = timeline.preview_ripple_delete_intervals(
            timeline_intervals
        )
        before_clips = {clip.id: clip for clip in before_state.clips}
        after_clips = {clip.id: clip for clip in after_state.clips}
        changed_clip_ids = sorted(
            clip_id
            for clip_id, clip in before_clips.items()
            if clip_id not in after_clips or after_clips[clip_id] != clip
        )
        created_clip_ids = sorted(set(after_clips).difference(before_clips))
        affected_track_ids = {
            clip.track_id
            for clip_id, clip in before_clips.items()
            if clip_id in changed_clip_ids
        } | {
            after_clips[clip_id].track_id
            for clip_id in created_clip_ids
        }
        subtitle_track_ids = {
            track.id
            for track in before_state.tracks
            if track.kind == TrackKind.SUBTITLE
            and any(
                placement.segment_id in segments_by_id
                for placement in self.repository.subtitles.list_subtitle_placements(track.id)
            )
        }
        affected_track_ids.update(subtitle_track_ids)
        first_cut = timeline_intervals[0][0]
        locked_track_ids = sorted(
            {
                track.id
                for track in before_state.tracks
                if track.locked
                and (
                    track.id in subtitle_track_ids
                    or any(
                        clip.track_id == track.id and clip.timeline_end > first_cut
                        for clip in before_state.clips
                    )
                )
            }
        )
        warnings = (
            [
                "锁定轨道不会随删除区间移动，应用后可能与其它轨道失去同步："
                + ", ".join(locked_track_ids)
            ]
            if locked_track_ids
            else []
        )
        plan = TranscriptEditPlan(
            sequence_id=request.sequence_id,
            document_id=request.document_id,
            expected_content_revision=request.expected_content_revision,
            main_profile=main_profile,
            sequence_profile=sequence_profile,
            selections=request.selections,
            resolved_selections=resolved,
            subtitle_intervals=[
                TranscriptFrameInterval(start_frame=start, end_frame=end)
                for start, end in subtitle_intervals
            ],
            timeline_intervals=[
                TranscriptFrameInterval(start_frame=start, end_frame=end)
                for start, end in timeline_intervals
            ],
            impact=TranscriptEditImpact(
                before_duration_frames=before_state.duration_frames,
                after_duration_frames=after_state.duration_frames,
                removed_duration_frames=sum(
                    end - start for start, end in timeline_intervals
                ),
                affected_track_ids=sorted(affected_track_ids),
                changed_clip_ids=changed_clip_ids,
                created_clip_ids=created_clip_ids,
                locked_track_ids=locked_track_ids,
            ),
            warnings=warnings,
            plan_digest="pending",
        )
        return plan.model_copy(update={"plan_digest": self._plan_digest(plan)})

    def apply_plan(
        self,
        plan: TranscriptEditPlan,
        timeline: TimelineEditor,
    ) -> TranscriptEditResult:
        if self._plan_digest(plan) != plan.plan_digest:
            raise ValueError("转录剪辑计划摘要无效，请重新预检")
        self._require_plan_profiles(plan, timeline)
        request = TranscriptEditRequest(
            sequence_id=plan.sequence_id,
            document_id=plan.document_id,
            expected_content_revision=plan.expected_content_revision,
            selections=plan.selections,
        )
        current_plan = self.preview_plan(request, timeline)
        if current_plan.plan_digest != plan.plan_digest:
            raise RuntimeError("Transcript edit conflict: previewed plan no longer matches project")

        before_state = timeline.state
        before_segments = self.repository.subtitles.list_subtitle_segments(plan.document_id)
        before_words = self.repository.subtitles.list_subtitle_words(plan.document_id)
        selected_segment_ids = {
            item_id
            for selection in plan.selections
            if selection.kind == "segments"
            for item_id in selection.ids
        }
        selected_word_ids = {
            item_id
            for selection in plan.selections
            if selection.kind == "words"
            for item_id in selection.ids
        }
        selected_word_ids.update(
            word.id for word in before_words if word.segment_id in selected_segment_ids
        )
        subtitle_intervals = [
            (interval.start_frame, interval.end_frame)
            for interval in plan.subtitle_intervals
        ]
        timeline_intervals = [
            (interval.start_frame, interval.end_frame)
            for interval in plan.timeline_intervals
        ]
        history_checkpoint = self.history.checkpoint()
        try:
            with self.repository.transaction():
                recovery = self.repository.records.create_project_version(
                    f"AI 转录剪辑前 · {plan.plan_digest[:8]}"
                )
                temporary_history = ProjectEditHistory()

                def apply_change():
                    temporary_timeline = TimelineEditor(
                        self.repository,
                        timeline.sequence_id,
                        temporary_history,
                    )
                    temporary_timeline.apply_ripple_delete_intervals(
                        timeline_intervals
                    )
                    after_words = [
                        self._remap_word(
                            word,
                            subtitle_intervals,
                            excluded=(
                                word.excluded
                                or word.id in selected_word_ids
                            ),
                        )
                        for word in before_words
                    ]
                    after_segments, retained_words = (
                        self._rebuild_segments(
                            before_segments,
                            after_words,
                            subtitle_intervals,
                            removed_segment_ids=(
                                selected_segment_ids
                            ),
                        )
                    )
                    self.repository.subtitles.save_subtitle_segments(
                        plan.document_id,
                        after_segments,
                    )
                    self.repository.subtitles.save_subtitle_words(
                        plan.document_id,
                        retained_words,
                    )
                    return self.repository.timeline.load_timeline(
                        timeline.sequence_id
                    )

                after_state, _output = (
                    self.publication.commit_document_change(
                        plan.document_id,
                        apply_change,
                    )
                )
                timeline.reload()
                after_segments = (
                    self.repository.subtitles.list_subtitle_segments(
                        plan.document_id
                    )
                )
                after_words = (
                    self.repository.subtitles.list_subtitle_words(
                        plan.document_id
                    )
                )

                def restore(
                    source_state,
                    destination_state,
                    segments,
                    words,
                ) -> None:
                    def restore_change() -> None:
                        self.repository.subtitles.save_subtitle_segments(
                            plan.document_id,
                            list(segments),
                        )
                        self.repository.subtitles.save_subtitle_words(
                            plan.document_id,
                            list(words),
                        )
                        timeline.restore_snapshot(
                            source_state,
                            destination_state,
                        )

                    self.publication.commit_document_change(
                        plan.document_id,
                        restore_change,
                    )

                self.history.push(
                    ProjectEditCommand(
                        label="AI 转录剪辑",
                        undo_action=lambda: restore(
                            after_state,
                            before_state,
                            before_segments,
                            before_words,
                        ),
                        redo_action=lambda: restore(
                            before_state,
                            after_state,
                            after_segments,
                            after_words,
                        ),
                    )
                )
                return TranscriptEditResult(
                    plan_digest=plan.plan_digest,
                    recovery_version=recovery,
                    removed_word_count=sum(
                        not word.excluded
                        and word.id in selected_word_ids
                        for word in before_words
                    ),
                    removed_segment_count=(
                        len(before_segments) - len(after_segments)
                    ),
                    before_duration_frames=(
                        before_state.duration_frames
                    ),
                    after_duration_frames=(
                        after_state.duration_frames
                    ),
                    content_revision=(
                        self.repository.content_revision()
                    ),
                )
        except BaseException as error:
            self.history.restore(history_checkpoint)
            try:
                timeline.reload()
            except BaseException as reload_error:
                error.add_note(
                    "转录剪辑回滚后重新载入时间轴失败："
                    f"{reload_error}"
                )
            raise

    def _source_transcript(
        self,
        sequence_id: str,
        document_id: str | None,
    ) -> SubtitleDocument:
        if document_id:
            document = self.repository.subtitles.get_subtitle_document(document_id)
            candidates = [document]
        else:
            candidates = self.repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)
        matching = [
            document
            for document in candidates
            if document.sequence_id == sequence_id
            and document.is_source
            and document.source_document_id is None
            and document.purpose == "sequence_transcript"
        ]
        if not matching:
            raise RuntimeError("当前时间轴还没有可供 AI 剪辑的源转录")
        return matching[-1]

    def _require_content_revision(self, expected: int) -> None:
        current = self.repository.content_revision()
        if current != expected:
            raise RuntimeError(
                "Transcript edit conflict: "
                f"expected project revision {expected}, current revision {current}"
            )

    def _require_plan_profiles(
        self,
        plan: TranscriptEditPlan,
        timeline: TimelineEditor,
    ) -> None:
        if timeline.sequence_id != plan.sequence_id:
            raise ValueError("转录剪辑计划与当前时间轴不一致")
        project = self.repository.catalog.get_project()
        main_profile = self.repository.catalog.get_sequence(
            project.main_sequence_id
        ).profile
        sequence_profile = self.repository.catalog.get_sequence(
            plan.sequence_id
        ).profile
        if (
            main_profile != plan.main_profile
            or sequence_profile != plan.sequence_profile
            or timeline.state.sequence.profile != plan.sequence_profile
        ):
            raise RuntimeError(
                "Transcript edit conflict: frame rate changed after preview"
            )

    @staticmethod
    def _validate_targets(
        selected_segment_ids: set[str],
        selected_word_ids: set[str],
        segments_by_id: dict[str, SubtitleSegment],
        words_by_id: dict[str, SubtitleWord],
    ) -> None:
        missing_segments = selected_segment_ids.difference(segments_by_id)
        if missing_segments:
            raise ValueError(f"转录剪辑计划包含无效字幕段：{sorted(missing_segments)}")
        missing_words = selected_word_ids.difference(words_by_id)
        if missing_words:
            raise ValueError(f"转录剪辑计划包含无效词语：{sorted(missing_words)}")
        selected_words = [words_by_id[word_id] for word_id in selected_word_ids]
        excluded = [word.id for word in selected_words if word.excluded]
        if excluded:
            raise ValueError(f"转录剪辑计划包含已经移除的词语：{sorted(excluded)}")
        estimated = [word.id for word in selected_words if word.timing_source != "recognized"]
        if estimated:
            raise ValueError(
                "估算词时间不能用于词级剪辑；请改为选择这些词所属的完整字幕段："
                + ", ".join(sorted(estimated))
            )
        duplicated_by_segment = [
            word.id
            for word in selected_words
            if word.segment_id in selected_segment_ids
        ]
        if duplicated_by_segment:
            raise ValueError(
                "同一计划不能同时选择完整字幕段及其中词语："
                + ", ".join(sorted(duplicated_by_segment))
            )

    @staticmethod
    def _plan_digest(plan: TranscriptEditPlan) -> str:
        payload = plan.model_dump(mode="json", exclude={"plan_digest"})
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _merged_intervals(
        intervals: Iterable[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        merged: list[tuple[int, int]] = []
        for start, end in sorted(
            ((int(start), int(end)) for start, end in intervals),
            key=lambda item: (item[0], item[1]),
        ):
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        return merged

    @classmethod
    def _remap_word(
        cls,
        word: SubtitleWord,
        intervals: list[tuple[int, int]],
        *,
        excluded: bool,
    ) -> SubtitleWord:
        start = cls._collapse_frame(word.start_frame, intervals)
        end = cls._collapse_frame(word.end_frame, intervals)
        if excluded or end <= start:
            end = start + 1
        return word.model_copy(
            update={"start_frame": start, "end_frame": end, "excluded": excluded}
        )

    @classmethod
    def _rebuild_segments(
        cls,
        segments: list[SubtitleSegment],
        words: list[SubtitleWord],
        intervals: list[tuple[int, int]],
        *,
        removed_segment_ids: set[str],
    ) -> tuple[list[SubtitleSegment], list[SubtitleWord]]:
        words_by_segment: dict[str, list[SubtitleWord]] = {}
        for word in words:
            words_by_segment.setdefault(word.segment_id, []).append(word)
        output_segments: list[SubtitleSegment] = []
        output_words: list[SubtitleWord] = []
        for segment in segments:
            if segment.id in removed_segment_ids:
                continue
            segment_words = sorted(
                words_by_segment.get(segment.id, []),
                key=lambda item: (item.position, item.start_frame, item.id),
            )
            active = [word for word in segment_words if not word.excluded]
            if segment_words and not active:
                continue
            if not segment_words:
                start = cls._collapse_frame(segment.start_frame, intervals)
                end = max(start + 1, cls._collapse_frame(segment.end_frame, intervals))
                output_segments.append(
                    segment.model_copy(update={"start_frame": start, "end_frame": end})
                )
                continue
            rebuilt = segment.model_copy(
                update={
                    "start_frame": min(word.start_frame for word in active),
                    "end_frame": max(word.end_frame for word in active),
                    "text": cls._join_words(active),
                    "confidence": None,
                }
            )
            output_segments.append(rebuilt)
            output_words.extend(
                word.model_copy(update={"position": position})
                for position, word in enumerate(segment_words)
            )
        retained_ids = {segment.id for segment in output_segments}
        return output_segments, [
            word for word in output_words if word.segment_id in retained_ids
        ]

    @staticmethod
    def _collapse_frame(frame: int, intervals: list[tuple[int, int]]) -> int:
        shift = 0
        for start, end in intervals:
            if frame < start:
                break
            if frame < end:
                return start - shift
            shift += end - start
        return frame - shift

    @staticmethod
    def _join_words(words: list[SubtitleWord]) -> str:
        values = [word.text.strip() for word in words if word.text.strip()]
        if any(re.search(r"[\u3400-\u9fff]", value) for value in values):
            return "".join(values)
        text = " ".join(values)
        return re.sub(r"\s+([,.;:!?%])", r"\1", text).strip()
