from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from mediaflow.application.edit_history import (
    ProjectEditAction,
    ProjectEditCommand,
    ProjectEditHistory,
)
from mediaflow.application.ports import TranscriptEditingDocuments
from mediaflow.application.project_changes import (
    entity_sequence_change_set,
    project_path_segment,
    timeline_change_set,
)
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.timeline_interval_move import TimelineIntervalMovePolicy
from mediaflow.application.timeline_ripple import RippleDeletePolicy
from mediaflow.domain.collaboration import ProjectChangeSet
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.project_records import ProjectVersionRecord
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.domain.timebase import reframe_frames, reframe_interval
from mediaflow.domain.timeline import TimelineState
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


@dataclass(frozen=True, slots=True)
class ScriptTimelineEditOutcome:
    segment: SubtitleSegment
    recovery_version: ProjectVersionRecord
    content_revision: int
    before_duration_frames: int
    after_duration_frames: int
    changed_timeline_frames: int


class TranscriptEditingService:
    """Preview and apply revision-bound transcript edit plans for automation clients."""

    def __init__(
        self,
        repository: TranscriptEditingDocuments,
        publication: SubtitlePublicationService,
        history: ProjectEditHistory,
        timeline_provider: Callable[[str], TimelineEditor] | None = None,
    ):
        self.repository = repository
        self.publication = publication
        self.history = history
        self._timeline_provider = timeline_provider
        self.history.register_handler(
            "transcript.restore",
            self._apply_history_action,
        )

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
            recognized_word_count=sum(word.timing_source == "recognized" for word in words),
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
        project = self.repository.projects.get_project()
        main_profile = self.repository.sequences.get_sequence(project.main_sequence_id).profile
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
                timing: Literal["recognized_words", "subtitle_segments"] = "recognized_words"
            else:
                selected_segments = sorted(
                    (segments_by_id[item_id] for item_id in selection.ids),
                    key=lambda item: (item.start_frame, item.end_frame, item.id),
                )
                selection_intervals = self._merged_intervals(
                    (segment.start_frame, segment.end_frame) for segment in selected_segments
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
        after_state = timeline.preview_ripple_delete_intervals(timeline_intervals)
        before_clips = {clip.id: clip for clip in before_state.clips}
        after_clips = {clip.id: clip for clip in after_state.clips}
        changed_clip_ids = sorted(
            clip_id
            for clip_id, clip in before_clips.items()
            if clip_id not in after_clips or after_clips[clip_id] != clip
        )
        created_clip_ids = sorted(set(after_clips).difference(before_clips))
        affected_track_ids = {
            clip.track_id for clip_id, clip in before_clips.items() if clip_id in changed_clip_ids
        } | {after_clips[clip_id].track_id for clip_id in created_clip_ids}
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
            ["锁定轨道不会随删除区间移动，应用后可能与其它轨道失去同步：" + ", ".join(locked_track_ids)]
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
                TranscriptFrameInterval(start_frame=start, end_frame=end) for start, end in subtitle_intervals
            ],
            timeline_intervals=[
                TranscriptFrameInterval(start_frame=start, end_frame=end) for start, end in timeline_intervals
            ],
            impact=TranscriptEditImpact(
                before_duration_frames=before_state.duration_frames,
                after_duration_frames=after_state.duration_frames,
                removed_duration_frames=sum(end - start for start, end in timeline_intervals),
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
            item_id for selection in plan.selections if selection.kind == "words" for item_id in selection.ids
        }
        selected_word_ids.update(word.id for word in before_words if word.segment_id in selected_segment_ids)
        subtitle_intervals = [
            (interval.start_frame, interval.end_frame) for interval in plan.subtitle_intervals
        ]
        timeline_intervals = [
            (interval.start_frame, interval.end_frame) for interval in plan.timeline_intervals
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
                    temporary_timeline.apply_ripple_delete_intervals(timeline_intervals)
                    after_words = [
                        self._remap_word(
                            word,
                            subtitle_intervals,
                            excluded=(word.excluded or word.id in selected_word_ids),
                        )
                        for word in before_words
                    ]
                    after_segments, retained_words = self._rebuild_segments(
                        before_segments,
                        after_words,
                        subtitle_intervals,
                        removed_segment_ids=(selected_segment_ids),
                    )
                    self.repository.subtitles.save_subtitle_segments(
                        plan.document_id,
                        after_segments,
                    )
                    self.repository.subtitles.save_subtitle_words(
                        plan.document_id,
                        retained_words,
                    )
                    return self.repository.timeline.load_timeline(timeline.sequence_id)

                after_state, _output = self.publication.commit_document_change(
                    plan.document_id,
                    apply_change,
                )
                timeline.reload()
                after_segments = self.repository.subtitles.list_subtitle_segments(plan.document_id)
                after_words = self.repository.subtitles.list_subtitle_words(plan.document_id)

                subtitle_root = f"/subtitles/documents/{project_path_segment(plan.document_id)}"
                self.history.push(
                    ProjectEditCommand(
                        label="AI 转录剪辑",
                        undo_actions=[
                            self._history_action(
                                timeline.sequence_id,
                                plan.document_id,
                                after_state,
                                before_state,
                                before_segments,
                                before_words,
                            )
                        ],
                        redo_actions=[
                            self._history_action(
                                timeline.sequence_id,
                                plan.document_id,
                                before_state,
                                after_state,
                                after_segments,
                                after_words,
                            )
                        ],
                    ),
                    ProjectChangeSet.combine(
                        [
                            timeline_change_set(before_state, after_state),
                            entity_sequence_change_set(
                                f"{subtitle_root}/segments",
                                before_segments,
                                after_segments,
                            ),
                            entity_sequence_change_set(
                                f"{subtitle_root}/words",
                                before_words,
                                after_words,
                            ),
                        ]
                    ),
                )
                return TranscriptEditResult(
                    plan_digest=plan.plan_digest,
                    recovery_version=recovery,
                    removed_word_count=sum(
                        not word.excluded and word.id in selected_word_ids for word in before_words
                    ),
                    removed_segment_count=(len(before_segments) - len(after_segments)),
                    before_duration_frames=(before_state.duration_frames),
                    after_duration_frames=(after_state.duration_frames),
                    content_revision=(self.repository.content_revision()),
                )
        except BaseException as error:
            self.history.restore(history_checkpoint)
            try:
                timeline.reload()
            except BaseException as reload_error:
                error.add_note(f"转录剪辑回滚后重新载入时间轴失败：{reload_error}")
            raise

    def close_script_gap(
        self,
        sequence_id: str,
        document_id: str,
        segment_id: str,
        *,
        expected_content_revision: int,
        timeline: TimelineEditor,
    ) -> ScriptTimelineEditOutcome:
        self._require_content_revision(expected_content_revision)
        document = self._source_transcript(sequence_id, document_id)
        if document.id != document_id or timeline.sequence_id != sequence_id:
            raise ValueError("脚本收缝目标与当前时间轴不一致")
        before_segments = self.repository.subtitles.list_subtitle_segments(document_id)
        try:
            target = next(item for item in before_segments if item.id == segment_id)
        except StopIteration as error:
            raise KeyError(segment_id) from error
        previous_end = max(
            (
                item.end_frame
                for item in before_segments
                if item.id != segment_id and item.end_frame <= target.start_frame
            ),
            default=0,
        )
        if previous_end >= target.start_frame:
            raise ValueError("这个脚本段落前没有可收起的静音间隙")

        project = self.repository.projects.get_project()
        main_profile = self.repository.sequences.get_sequence(project.main_sequence_id).profile
        timeline_start, timeline_end = reframe_interval(
            previous_end,
            target.start_frame,
            main_profile,
            timeline.state.sequence.profile,
        )
        locked = [
            track.id
            for track in timeline.state.tracks
            if track.locked
            and (
                track.kind == TrackKind.SUBTITLE
                or any(
                    clip.track_id == track.id and clip.timeline_end > timeline_start
                    for clip in timeline.state.clips
                )
            )
        ]
        if locked:
            raise ValueError("收缝会移动锁定轨道，请先解锁：" + ", ".join(sorted(locked)))
        before_state = timeline.state
        after_state = before_state.model_copy(deep=True)
        RippleDeletePolicy.apply(after_state, timeline_start, timeline_end)
        before_words = self.repository.subtitles.list_subtitle_words(document_id)
        subtitle_intervals = [(previous_end, target.start_frame)]
        remapped_words = [
            self._remap_word(word, subtitle_intervals, excluded=word.excluded)
            for word in before_words
        ]
        after_segments, after_words = self._rebuild_segments(
            before_segments,
            remapped_words,
            subtitle_intervals,
            removed_segment_ids=set(),
        )
        moved_target = next(item for item in after_segments if item.id == segment_id)
        recovery = self._commit_script_timeline_change(
            label="收起脚本静音间隙",
            recovery_label=f"脚本收缝前 · {segment_id[:8]}",
            timeline=timeline,
            document_id=document_id,
            before_state=before_state,
            after_state=after_state,
            before_segments=before_segments,
            after_segments=after_segments,
            before_words=before_words,
            after_words=after_words,
        )
        return ScriptTimelineEditOutcome(
            segment=moved_target,
            recovery_version=recovery,
            content_revision=self.repository.content_revision(),
            before_duration_frames=before_state.duration_frames,
            after_duration_frames=after_state.duration_frames,
            changed_timeline_frames=timeline_end - timeline_start,
        )

    def move_script_segment(
        self,
        sequence_id: str,
        document_id: str,
        segment_id: str,
        *,
        position: int,
        expected_content_revision: int,
        timeline: TimelineEditor,
    ) -> ScriptTimelineEditOutcome:
        self._require_content_revision(expected_content_revision)
        document = self._source_transcript(sequence_id, document_id)
        if document.id != document_id or timeline.sequence_id != sequence_id:
            raise ValueError("脚本重排目标与当前时间轴不一致")
        before_segments = self.repository.subtitles.list_subtitle_segments(document_id)
        if not 0 <= position < len(before_segments):
            raise ValueError("脚本段落目标位置超出范围")
        try:
            current_position = next(
                index for index, item in enumerate(before_segments) if item.id == segment_id
            )
        except StopIteration as error:
            raise KeyError(segment_id) from error
        if current_position == position:
            raise ValueError("脚本段落已经位于目标位置")
        source = before_segments[current_position]
        overlaps = [
            item.id
            for item in before_segments
            if item.id != source.id
            and item.start_frame < source.end_frame
            and item.end_frame > source.start_frame
        ]
        if overlaps:
            raise ValueError("重排段落与其它脚本时间重叠，请先修复重叠：" + ", ".join(overlaps))
        remaining = [item for item in before_segments if item.id != source.id]
        destination = (
            remaining[position].start_frame
            if position < len(remaining)
            else remaining[-1].end_frame
        )
        main_move = TimelineIntervalMovePolicy(
            source.start_frame,
            source.end_frame,
            destination,
        )
        project = self.repository.projects.get_project()
        main_profile = self.repository.sequences.get_sequence(project.main_sequence_id).profile
        sequence_profile = timeline.state.sequence.profile
        timeline_start, timeline_end = reframe_interval(
            source.start_frame,
            source.end_frame,
            main_profile,
            sequence_profile,
        )
        timeline_destination = reframe_frames(
            destination,
            main_profile,
            sequence_profile,
        )
        timeline_move = TimelineIntervalMovePolicy(
            timeline_start,
            timeline_end,
            timeline_destination,
        )
        before_state = timeline.state
        after_state = before_state.model_copy(deep=True)
        timeline_move.apply(after_state)

        segment_shifts = {
            item.id: main_move.shift_for_interval(item.start_frame, item.end_frame)
            for item in before_segments
        }
        after_segments = sorted(
            (
                item.model_copy(
                    update={
                        "start_frame": item.start_frame + segment_shifts[item.id],
                        "end_frame": item.end_frame + segment_shifts[item.id],
                    }
                )
                for item in before_segments
            ),
            key=lambda item: (item.start_frame, item.id),
        )
        before_words = self.repository.subtitles.list_subtitle_words(document_id)
        after_words = [
            word.model_copy(
                update={
                    "start_frame": word.start_frame + segment_shifts[word.segment_id],
                    "end_frame": word.end_frame + segment_shifts[word.segment_id],
                }
            )
            for word in before_words
        ]
        moved_target = next(item for item in after_segments if item.id == segment_id)
        recovery = self._commit_script_timeline_change(
            label="重排脚本段落",
            recovery_label=f"脚本重排前 · {segment_id[:8]}",
            timeline=timeline,
            document_id=document_id,
            before_state=before_state,
            after_state=after_state,
            before_segments=before_segments,
            after_segments=after_segments,
            before_words=before_words,
            after_words=after_words,
        )
        return ScriptTimelineEditOutcome(
            segment=moved_target,
            recovery_version=recovery,
            content_revision=self.repository.content_revision(),
            before_duration_frames=before_state.duration_frames,
            after_duration_frames=after_state.duration_frames,
            changed_timeline_frames=abs(timeline_destination - timeline_start),
        )

    def _commit_script_timeline_change(
        self,
        *,
        label: str,
        recovery_label: str,
        timeline: TimelineEditor,
        document_id: str,
        before_state: TimelineState,
        after_state: TimelineState,
        before_segments: list[SubtitleSegment],
        after_segments: list[SubtitleSegment],
        before_words: list[SubtitleWord],
        after_words: list[SubtitleWord],
    ) -> ProjectVersionRecord:
        history_checkpoint = self.history.checkpoint()
        try:
            with self.repository.transaction():
                recovery = self.repository.records.create_project_version(recovery_label)

                def apply_change() -> TimelineState:
                    temporary_timeline = TimelineEditor(
                        self.repository,
                        timeline.sequence_id,
                        ProjectEditHistory(),
                    )
                    temporary_timeline.restore_snapshot(before_state, after_state)
                    self.repository.subtitles.save_subtitle_segments(
                        document_id,
                        after_segments,
                    )
                    self.repository.subtitles.save_subtitle_words(
                        document_id,
                        after_words,
                    )
                    return self.repository.timeline.load_timeline(timeline.sequence_id)

                persisted_state, _output = self.publication.commit_document_change(
                    document_id,
                    apply_change,
                )
                timeline.reload()
                persisted_segments = self.repository.subtitles.list_subtitle_segments(document_id)
                persisted_words = self.repository.subtitles.list_subtitle_words(document_id)
                subtitle_root = f"/subtitles/documents/{project_path_segment(document_id)}"
                self.history.push(
                    ProjectEditCommand(
                        label=label,
                        undo_actions=[
                            self._history_action(
                                timeline.sequence_id,
                                document_id,
                                persisted_state,
                                before_state,
                                before_segments,
                                before_words,
                            )
                        ],
                        redo_actions=[
                            self._history_action(
                                timeline.sequence_id,
                                document_id,
                                before_state,
                                persisted_state,
                                persisted_segments,
                                persisted_words,
                            )
                        ],
                    ),
                    ProjectChangeSet.combine(
                        [
                            timeline_change_set(before_state, persisted_state),
                            entity_sequence_change_set(
                                f"{subtitle_root}/segments",
                                before_segments,
                                persisted_segments,
                            ),
                            entity_sequence_change_set(
                                f"{subtitle_root}/words",
                                before_words,
                                persisted_words,
                            ),
                        ]
                    ),
                )
                return recovery
        except BaseException as error:
            self.history.restore(history_checkpoint)
            try:
                timeline.reload()
            except BaseException as reload_error:
                error.add_note(f"脚本时间线编辑回滚后重新载入失败：{reload_error}")
            raise

    @staticmethod
    def _history_action(
        sequence_id: str,
        document_id: str,
        source_state: TimelineState,
        destination_state: TimelineState,
        segments: list[SubtitleSegment],
        words: list[SubtitleWord],
    ) -> ProjectEditAction:
        return ProjectEditAction(
            kind="transcript.restore",
            payload={
                "sequence_id": sequence_id,
                "document_id": document_id,
                "source_state": source_state.model_dump(mode="json", exclude_computed_fields=True),
                "destination_state": destination_state.model_dump(mode="json", exclude_computed_fields=True),
                "segments": [item.model_dump(mode="json", exclude_computed_fields=True) for item in segments],
                "words": [item.model_dump(mode="json", exclude_computed_fields=True) for item in words],
            },
        )

    def _apply_history_action(self, action: ProjectEditAction) -> None:
        payload = action.payload
        sequence_id = str(payload.get("sequence_id") or "")
        document_id = str(payload.get("document_id") or "")
        timeline = (
            self._timeline_provider(sequence_id)
            if self._timeline_provider is not None
            else TimelineEditor(self.repository, sequence_id)
        )

        def restore_change() -> None:
            self.repository.subtitles.save_subtitle_segments(
                document_id,
                [SubtitleSegment.model_validate(item) for item in payload.get("segments") or []],
            )
            self.repository.subtitles.save_subtitle_words(
                document_id,
                [SubtitleWord.model_validate(item) for item in payload.get("words") or []],
            )
            timeline.restore_snapshot(
                TimelineState.model_validate(payload.get("source_state")),
                TimelineState.model_validate(payload.get("destination_state")),
            )

        self.publication.commit_document_change(document_id, restore_change)

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
                f"Transcript edit conflict: expected project revision {expected}, current revision {current}"
            )

    def _require_plan_profiles(
        self,
        plan: TranscriptEditPlan,
        timeline: TimelineEditor,
    ) -> None:
        if timeline.sequence_id != plan.sequence_id:
            raise ValueError("转录剪辑计划与当前时间轴不一致")
        project = self.repository.projects.get_project()
        main_profile = self.repository.sequences.get_sequence(project.main_sequence_id).profile
        sequence_profile = self.repository.sequences.get_sequence(plan.sequence_id).profile
        if (
            main_profile != plan.main_profile
            or sequence_profile != plan.sequence_profile
            or timeline.state.sequence.profile != plan.sequence_profile
        ):
            raise RuntimeError("Transcript edit conflict: frame rate changed after preview")

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
            word.id for word in selected_words if word.segment_id in selected_segment_ids
        ]
        if duplicated_by_segment:
            raise ValueError(
                "同一计划不能同时选择完整字幕段及其中词语：" + ", ".join(sorted(duplicated_by_segment))
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
        return word.model_copy(update={"start_frame": start, "end_frame": end, "excluded": excluded})

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
                output_segments.append(segment.model_copy(update={"start_frame": start, "end_frame": end}))
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
                word.model_copy(update={"position": position}) for position, word in enumerate(segment_words)
            )
        retained_ids = {segment.id for segment in output_segments}
        return output_segments, [word for word in output_words if word.segment_id in retained_ids]

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
