from __future__ import annotations

import re
from functools import wraps

from mediaflow.application.edit_history import ProjectEditCommand, ProjectEditHistory
from mediaflow.application.ports import SubtitleEditingDocuments
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.domain.subtitles import SubtitlePlacement, SubtitleSegment
from mediaflow.domain.timebase import seconds_to_frames


def recorded_subtitle_edit(label: str):
    def decorate(method):
        @wraps(method)
        def wrapped(self, document_id: str, *args, **kwargs):
            if self.history is None:
                return method(self, document_id, *args, **kwargs)
            before = self.repository.list_subtitle_segments(document_id)
            result = method(self, document_id, *args, **kwargs)
            after = self.repository.list_subtitle_segments(document_id)
            if before != after:
                self.history.push(
                    ProjectEditCommand(
                        label=label,
                        undo_action=lambda: self._save_segments(document_id, list(before)),
                        redo_action=lambda: self._save_segments(document_id, list(after)),
                    )
                )
            return result

        return wrapped

    return decorate


class SubtitleEditingService:
    """Edit persisted subtitle segments and record undoable document changes."""

    def __init__(
        self,
        repository: SubtitleEditingDocuments,
        publication: SubtitlePublicationService,
        history: ProjectEditHistory | None = None,
    ):
        self.repository = repository
        self.publication = publication
        self.history = history

    def update_placement_range(
        self,
        placement_id: str,
        *,
        start_frame: int,
        end_frame: int,
    ) -> SubtitlePlacement:
        before = self.repository.get_subtitle_placement(placement_id)
        after = self.repository.update_subtitle_placement_range(
            placement_id,
            start_frame,
            end_frame,
            timing_overridden=True,
        )
        if self.history is not None and before != after:
            self.history.push(
                ProjectEditCommand(
                    label="调整序列字幕时间",
                    undo_action=lambda: self._restore_placement_range(
                        before.id,
                        before.start_frame,
                        before.end_frame,
                        timing_overridden=before.timing_overridden,
                    ),
                    redo_action=lambda: self._restore_placement_range(
                        after.id,
                        after.start_frame,
                        after.end_frame,
                        timing_overridden=after.timing_overridden,
                    ),
                )
            )
        return after

    def reset_placement_range(self, placement_id: str) -> SubtitlePlacement:
        before = self.repository.get_subtitle_placement(placement_id)
        after = self.repository.reset_subtitle_placement_range(placement_id)
        if self.history is not None and before != after:
            self.history.push(
                ProjectEditCommand(
                    label="恢复序列字幕时间",
                    undo_action=lambda: self._restore_placement_range(
                        before.id,
                        before.start_frame,
                        before.end_frame,
                        timing_overridden=before.timing_overridden,
                    ),
                    redo_action=lambda: self._restore_placement_range(
                        after.id,
                        after.start_frame,
                        after.end_frame,
                        timing_overridden=after.timing_overridden,
                    ),
                )
            )
        return after

    def _restore_placement_range(
        self,
        placement_id: str,
        start_frame: int,
        end_frame: int,
        *,
        timing_overridden: bool,
    ) -> None:
        self.repository.update_subtitle_placement_range(
            placement_id,
            start_frame,
            end_frame,
            timing_overridden=timing_overridden,
        )

    @recorded_subtitle_edit("修改字幕")
    def update_segment(
        self,
        document_id: str,
        segment_id: str,
        *,
        start_frame: int,
        end_frame: int,
        text: str,
    ) -> SubtitleSegment:
        value = text.strip()
        if not value:
            raise ValueError("字幕文本不能为空")
        segments = self.repository.list_subtitle_segments(document_id)
        try:
            index = next(index for index, item in enumerate(segments) if item.id == segment_id)
        except StopIteration as error:
            raise KeyError(segment_id) from error
        updated = segments[index].model_copy(
            update={
                "start_frame": int(start_frame),
                "end_frame": int(end_frame),
                "text": value,
            }
        )
        segments[index] = updated
        self._save_segments(document_id, segments)
        return updated

    @recorded_subtitle_edit("添加字幕")
    def add_segment(
        self,
        document_id: str,
        *,
        start_frame: int,
        end_frame: int,
        text: str,
    ) -> SubtitleSegment:
        value = text.strip()
        if not value:
            raise ValueError("字幕文本不能为空")
        segment = SubtitleSegment(
            document_id=document_id,
            start_frame=int(start_frame),
            end_frame=int(end_frame),
            text=value,
        )
        segments = [*self.repository.list_subtitle_segments(document_id), segment]
        self._save_segments(document_id, segments)
        return segment

    @recorded_subtitle_edit("删除字幕")
    def delete_segments(self, document_id: str, segment_ids: list[str]) -> int:
        wanted = set(segment_ids)
        if not wanted:
            return 0
        segments = self.repository.list_subtitle_segments(document_id)
        remaining = [item for item in segments if item.id not in wanted]
        removed = len(segments) - len(remaining)
        if removed != len(wanted):
            raise KeyError("包含不属于当前字幕文档的字幕段")
        self._save_segments(document_id, remaining)
        return removed

    @recorded_subtitle_edit("合并字幕")
    def merge_segments(self, document_id: str, segment_ids: list[str]) -> SubtitleSegment:
        wanted = set(segment_ids)
        if len(wanted) < 2:
            raise ValueError("至少选择两条连续字幕才能合并")
        segments = self.repository.list_subtitle_segments(document_id)
        indexes = [index for index, item in enumerate(segments) if item.id in wanted]
        if len(indexes) != len(wanted):
            raise KeyError("包含不属于当前字幕文档的字幕段")
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise ValueError("只能合并连续字幕")
        selected = [segments[index] for index in indexes]
        speakers = {item.speaker for item in selected}
        merged = selected[0].model_copy(
            update={
                "start_frame": min(item.start_frame for item in selected),
                "end_frame": max(item.end_frame for item in selected),
                "text": " ".join(part for item in selected if (part := " ".join(item.text.split()))),
                "speaker": selected[0].speaker if len(speakers) == 1 else None,
                "confidence": None,
            }
        )
        updated = [*segments[: indexes[0]], merged, *segments[indexes[-1] + 1 :]]
        self._save_segments(document_id, updated)
        return merged

    @recorded_subtitle_edit("拆分字幕")
    def split_segment(
        self,
        document_id: str,
        segment_id: str,
        *,
        split_frame: int | None = None,
        split_index: int | None = None,
    ) -> tuple[SubtitleSegment, SubtitleSegment]:
        segments = self.repository.list_subtitle_segments(document_id)
        try:
            index = next(index for index, item in enumerate(segments) if item.id == segment_id)
        except StopIteration as error:
            raise KeyError(segment_id) from error
        first, second = self._split(segments[index], split_frame=split_frame, split_index=split_index)
        updated = [*segments[:index], first, second, *segments[index + 1 :]]
        self._save_segments(document_id, updated)
        return first, second

    @recorded_subtitle_edit("智能拆分字幕")
    def smart_split_document(self, document_id: str, *, text_limit: int = 24) -> int:
        limit = max(1, int(text_limit))
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        minimum_duration = max(
            2,
            seconds_to_frames(1.6, profile.fps_numerator, profile.fps_denominator),
        )
        segments = self.repository.list_subtitle_segments(document_id)
        updated: list[SubtitleSegment] = []
        split_count = 0
        for segment in segments:
            text = segment.text.strip()
            cjk = bool(re.search(r"[\u3000-\u303f\u3040-\u30ff\uff00-\uffef\u3400-\u9fff]", text))
            latin_limit = max(1, round(limit * 56 / 24))
            word_limit = max(1, round(limit * 11 / 24))
            long_enough = (
                len(text) >= limit if cjk else (len(text) >= latin_limit or len(text.split()) >= word_limit)
            )
            if segment.end_frame - segment.start_frame < minimum_duration or not long_enough:
                updated.append(segment)
                continue
            try:
                first, second = self._split(segment)
            except ValueError:
                updated.append(segment)
            else:
                updated.extend((first, second))
                split_count += 1
        if split_count:
            self._save_segments(document_id, updated)
        return split_count

    @recorded_subtitle_edit("修复字幕重叠")
    def fix_overlaps(self, document_id: str) -> int:
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        tolerance = max(
            1,
            seconds_to_frames(0.05, profile.fps_numerator, profile.fps_denominator),
        )
        segments = sorted(
            self.repository.list_subtitle_segments(document_id),
            key=lambda item: (item.start_frame, item.id),
        )
        fixed: list[SubtitleSegment] = []
        count = 0
        for segment in segments:
            if fixed and segment.start_frame < fixed[-1].end_frame - tolerance:
                duration = segment.end_frame - segment.start_frame
                new_start = fixed[-1].end_frame + tolerance
                segment = segment.model_copy(
                    update={"start_frame": new_start, "end_frame": new_start + duration}
                )
                count += 1
            fixed.append(segment)
        if count:
            self._save_segments(document_id, fixed)
        return count

    def selected_segments_srt(self, document_id: str, segment_ids: list[str]) -> str:
        selected = self._selected_segments(document_id, segment_ids)
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        return SubtitleFile.dumps_srt(
            [
                SubtitleCue(
                    start_frame=segment.start_frame,
                    end_frame=segment.end_frame,
                    text=segment.text,
                )
                for segment in selected
            ],
            fps_numerator=profile.fps_numerator,
            fps_denominator=profile.fps_denominator,
        )

    @recorded_subtitle_edit("粘贴替换字幕")
    def replace_selected_texts(
        self,
        document_id: str,
        segment_ids: list[str],
        clipboard_text: str,
    ) -> int:
        value = clipboard_text.strip()
        if not value:
            raise ValueError("剪贴板中没有可用文本")
        selected = self._selected_segments(document_id, segment_ids)
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        replacements: list[str] = []
        if "-->" in value:
            try:
                replacements = [
                    cue.text
                    for cue in SubtitleFile.parse_srt(
                        value,
                        fps_numerator=profile.fps_numerator,
                        fps_denominator=profile.fps_denominator,
                    )
                ]
            except ValueError:
                replacements = []
        if not replacements:
            replacements = [line.strip() for line in value.splitlines() if line.strip()]
        count = min(len(selected), len(replacements))
        if count == 0:
            raise ValueError("剪贴板中没有可替换的字幕文本")
        replacement_by_id = {selected[index].id: replacements[index] for index in range(count)}
        segments = self.repository.list_subtitle_segments(document_id)
        self._save_segments(
            document_id,
            [
                segment.model_copy(update={"text": replacement_by_id[segment.id]})
                if segment.id in replacement_by_id
                else segment
                for segment in segments
            ],
        )
        return count

    @recorded_subtitle_edit("替换字幕文本")
    def replace_all(
        self,
        document_id: str,
        search: str,
        replacement: str,
        *,
        match_case: bool = False,
    ) -> int:
        if not search:
            raise ValueError("查找内容不能为空")
        pattern = re.compile(re.escape(search), 0 if match_case else re.IGNORECASE)
        count = 0
        updated: list[SubtitleSegment] = []
        for segment in self.repository.list_subtitle_segments(document_id):
            value, replacements = pattern.subn(replacement, segment.text)
            if replacements and not value.strip():
                raise ValueError("替换后字幕文本不能为空")
            count += replacements
            updated.append(segment.model_copy(update={"text": value}) if replacements else segment)
        if count:
            self._save_segments(document_id, updated)
        return count

    @recorded_subtitle_edit("替换当前字幕文本")
    def replace_match(
        self,
        document_id: str,
        segment_id: str,
        start: int,
        end: int,
        search: str,
        replacement: str,
        *,
        match_case: bool = False,
    ) -> SubtitleSegment:
        if not search:
            raise ValueError("查找内容不能为空")
        segments = self.repository.list_subtitle_segments(document_id)
        try:
            segment = next(item for item in segments if item.id == segment_id)
        except StopIteration as error:
            raise KeyError(segment_id) from error
        if start < 0 or end <= start or end > len(segment.text):
            raise ValueError("当前查找结果已经失效，请重新查找")
        current = segment.text[start:end]
        matches = current == search if match_case else current.casefold() == search.casefold()
        if not matches:
            raise ValueError("当前查找结果已经失效，请重新查找")
        value = f"{segment.text[:start]}{replacement}{segment.text[end:]}"
        if not value.strip():
            raise ValueError("替换后字幕文本不能为空")
        updated_segment = segment.model_copy(update={"text": value})
        self._save_segments(
            document_id,
            [updated_segment if item.id == segment.id else item for item in segments],
        )
        return updated_segment

    def find_matches(
        self,
        document_id: str,
        search: str,
        *,
        match_case: bool = False,
    ) -> list[dict[str, int | str]]:
        if not search:
            return []
        pattern = re.compile(re.escape(search), 0 if match_case else re.IGNORECASE)
        return [
            {"segmentId": segment.id, "start": match.start(), "end": match.end()}
            for segment in self.repository.list_subtitle_segments(document_id)
            for match in pattern.finditer(segment.text)
        ]

    def _save_segments(self, document_id: str, segments: list[SubtitleSegment]) -> None:
        self.repository.save_subtitle_segments(document_id, segments)
        self.publication.write_document_srt(document_id)

    def _selected_segments(
        self,
        document_id: str,
        segment_ids: list[str],
    ) -> list[SubtitleSegment]:
        wanted = set(segment_ids)
        if not wanted:
            raise ValueError("请先选择字幕段")
        selected = [
            segment for segment in self.repository.list_subtitle_segments(document_id) if segment.id in wanted
        ]
        if len(selected) != len(wanted):
            raise KeyError("包含不属于当前字幕文档的字幕段")
        return selected

    @classmethod
    def _split(
        cls,
        segment: SubtitleSegment,
        *,
        split_frame: int | None = None,
        split_index: int | None = None,
    ) -> tuple[SubtitleSegment, SubtitleSegment]:
        text = segment.text.strip()
        if len(text) < 2 or segment.end_frame - segment.start_frame < 2:
            raise ValueError("字幕太短，无法拆分")
        if split_index is None:
            preferred = round(
                len(text)
                * (
                    (split_frame - segment.start_frame) / (segment.end_frame - segment.start_frame)
                    if split_frame is not None
                    else 0.5
                )
            )
            split_index = cls._nearest_split_index(text, preferred)
        if not 0 < split_index < len(text):
            raise ValueError("拆分位置必须位于字幕文本中间")
        if split_frame is None:
            split_frame = segment.start_frame + round(
                (segment.end_frame - segment.start_frame) * split_index / len(text)
            )
        split_frame = max(segment.start_frame + 1, min(segment.end_frame - 1, int(split_frame)))
        first_text = text[:split_index].rstrip()
        second_text = text[split_index:].lstrip()
        if not first_text or not second_text:
            raise ValueError("拆分后两段字幕都必须有文本")
        first = segment.model_copy(update={"end_frame": split_frame, "text": first_text, "confidence": None})
        second = SubtitleSegment(
            document_id=segment.document_id,
            source_segment_id=segment.source_segment_id,
            start_frame=split_frame,
            end_frame=segment.end_frame,
            text=second_text,
            speaker=segment.speaker,
            confidence=None,
        )
        return first, second

    @staticmethod
    def _nearest_split_index(text: str, preferred: int) -> int:
        preferred = max(1, min(len(text) - 1, preferred))
        candidates: list[tuple[int, int, int]] = []
        punctuation = "。！？!?；;，,：:、."
        for index in range(1, len(text)):
            boundary_strength = 0
            if text[index - 1] in punctuation:
                boundary_strength = 2
            elif text[index - 1].isspace() or text[index].isspace():
                boundary_strength = 1
            if boundary_strength:
                candidates.append((-boundary_strength, abs(index - preferred), index))
        return min(candidates)[2] if candidates else preferred
