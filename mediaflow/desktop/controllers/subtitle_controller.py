from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from mediaflow.desktop.presentation_catalogs import (
    translation_language_options,
    translation_mode_options,
)
from mediaflow.domain.asr import TranscriptionPlan
from mediaflow.domain.enums import (
    TrackKind,
)
from mediaflow.domain.sequence_audio import build_dialogue_transcription_plan
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.subtitles import SubtitleSegment
from mediaflow.domain.task_commands import (
    TranscribeSequenceCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)
from mediaflow.domain.timebase import reframe_frames

from .controller_facet import ControllerFacet, report_ui_errors


class SubtitleController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def subtitleDocumentsModel(self) -> QObject:
        return self._session.models.documents

    @Property(QObject, constant=True)
    def subtitleSegmentsModel(self) -> QObject:
        return self._session.models.segments

    @Property(QObject, constant=True)
    def subtitlePlacementsModel(self) -> QObject:
        return self._session.models.subtitle_placements

    @Property("QVariantList", constant=True)
    def translationModeOptions(self) -> list[dict]:
        return translation_mode_options()

    @Property("QVariantList", constant=True)
    def translationLanguageOptions(self) -> list[dict]:
        return translation_language_options()

    @Property(str, notify=selectionChanged)
    def selectedDocumentId(self) -> str:
        return self._session.selection.document_id

    @Property(str, notify=selectionChanged)
    def selectedSubtitleSegmentId(self) -> str:
        return (
            self._session.selection.subtitle_segment_ids[-1]
            if self._session.selection.subtitle_segment_ids
            else ""
        )

    @Property("QVariantList", notify=selectionChanged)
    def selectedSubtitleSegmentIds(self) -> list[str]:
        return list(self._session.selection.subtitle_segment_ids)

    @Property("QVariantMap", notify=selectionChanged)
    def selectedSubtitleSegmentData(self) -> dict:
        row = self._session.models.segments.findRow("segmentId", self.selectedSubtitleSegmentId)
        return self._session.models.segments.get(row)

    @Property(str, notify=selectionChanged)
    def selectedSubtitlePlacementId(self) -> str:
        return self._session.selection.subtitle_placement_id

    @Property("QVariantMap", notify=selectionChanged)
    def selectedSubtitlePlacementData(self) -> dict:
        row = self._session.models.subtitle_placements.findRow(
            "placementId", self._session.selection.subtitle_placement_id
        )
        return self._session.models.subtitle_placements.get(row)

    @Slot(str)
    def selectSubtitleDocument(self, document_id: str) -> None:
        self._session.selection.document_id = document_id
        self._session.selection.subtitle_segment_ids = []
        self._session.projectors.subtitles.refresh_segments()
        self._session.events.selectionChanged.emit()

    @Slot(str)
    @Slot(str, bool)
    def selectSubtitleSegment(self, segment_id: str, toggle: bool = False) -> None:
        self._session.selection.subtitle_segment_ids = self._session._updated_selection(
            self._session.selection.subtitle_segment_ids,
            segment_id,
            toggle=toggle,
        )
        self._session.events.selectionChanged.emit()

    @Slot(str, result=bool)
    def isSubtitleSegmentSelected(self, segment_id: str) -> bool:
        return segment_id in self._session.selection.subtitle_segment_ids

    @Slot(str)
    def selectSubtitlePlacement(self, placement_id: str) -> None:
        self._select_subtitle_placement_context(placement_id)

    @Slot(int)
    def followSubtitleAtFrame(self, frame: int) -> None:
        frame = max(0, int(frame))
        active = None
        for index in range(self._session.models.subtitle_placements.rowCount()):
            row = self._session.models.subtitle_placements.get(index)
            if int(row.get("startFrame", 0)) <= frame < int(row.get("endFrame", 0)):
                active = row
                if row.get("placementId") == self._session.selection.subtitle_placement_id:
                    break
        if active is None:
            if self._session.selection.subtitle_placement_id:
                self._session.selection.subtitle_placement_id = ""
                self._session.selection.subtitle_segment_ids = []
                self._session.events.selectionChanged.emit()
            return
        if active.get("placementId") != self._session.selection.subtitle_placement_id:
            self._select_subtitle_placement_context(str(active["placementId"]))

    @Slot(str)
    @report_ui_errors
    def previewSubtitlePlacement(self, placement_id: str) -> None:
        placement = self._session.binding.current.get_subtitle_placement(placement_id)
        self._select_subtitle_placement_context(placement_id)
        self._session.events.previewRangeRequested.emit(placement.start_frame, placement.end_frame)

    @Slot(str)
    @report_ui_errors
    def previewSubtitleSegment(self, segment_id: str) -> None:
        for index in range(self._session.models.subtitle_placements.rowCount()):
            row = self._session.models.subtitle_placements.get(index)
            if row.get("segmentId") == segment_id:
                self.previewSubtitlePlacement(str(row["placementId"]))
                return
        self.selectSubtitleSegment(segment_id)
        segment = next(
            item
            for item in self._session.binding.current.list_subtitle_segments(
                self._session.selection.document_id
            )
            if item.id == segment_id
        )
        start_frame = self._main_frame_in_active_clock(segment.start_frame)
        end_frame = max(
            start_frame + 1,
            self._main_frame_in_active_clock(segment.end_frame),
        )
        self._session.events.previewRangeRequested.emit(start_frame, end_frame)

    @Slot(str, int, result=int)
    def subtitleSegmentTimelineFrame(self, segment_id: str, fallback_frame: int) -> int:
        for index in range(self._session.models.subtitle_placements.rowCount()):
            row = self._session.models.subtitle_placements.get(index)
            if row.get("segmentId") == segment_id:
                return int(row.get("startFrame", fallback_frame))
        return self._main_frame_in_active_clock(fallback_frame)

    def _main_frame_in_active_clock(self, frame: int) -> int:
        current = self._session.binding.current
        active_sequence_id = self._session.binding.active_sequence_id
        if current is None or not active_sequence_id:
            return max(0, int(frame))
        project = current.get_project()
        main_profile = current.get_sequence(project.main_sequence_id).profile
        active_profile = current.get_sequence(active_sequence_id).profile
        return max(
            0,
            reframe_frames(
                int(frame),
                main_profile,
                active_profile,
            ),
        )

    @Property(bool, notify=historyChanged)
    def canTranscribeCurrentSequence(self) -> bool:
        try:
            return self._current_transcription_plan().region_count > 0
        except (RuntimeError, ValueError):
            return False

    @Property("QVariantMap", notify=historyChanged)
    def transcriptionPlanSummary(self) -> dict:
        try:
            plan = self._current_transcription_plan()
        except (RuntimeError, ValueError) as error:
            return {"available": False, "error": str(error)}
        return {
            "available": plan.region_count > 0,
            "timelineStartFrame": plan.timeline_start_frame,
            "timelineEndFrame": plan.timeline_end_frame,
            "timelineDurationFrames": (plan.timeline_end_frame - plan.timeline_start_frame),
            "recognitionSeconds": plan.recognition_seconds,
            "sourceCount": plan.source_count,
            "regionCount": plan.region_count,
            "engine": plan.asr.engine,
            "model": plan.asr.model,
            "device": plan.asr.device,
            "language": plan.asr.language,
            "parallelChunks": plan.asr.parallel_chunks,
        }

    @Slot(str, result="QVariantMap")
    def sequenceTranscriptionSummary(self, sequence_id: str) -> dict:
        if not sequence_id or not self._session.binding.current:
            return {}
        documents = [
            item
            for item in self._session.binding.current.list_subtitle_documents(sequence_id=sequence_id)
            if item.is_source and item.source_document_id is None and item.purpose == "sequence_transcript"
        ]
        if not documents:
            return {}
        document = documents[-1]
        segment_count, start_frame, end_frame = self._session.binding.current.subtitle_segment_summary(
            document.id
        )
        return {
            "documentId": document.id,
            "language": document.language,
            "segmentCount": segment_count,
            "startFrame": start_frame,
            "endFrame": end_frame,
        }

    @Slot(str, int, float, int, bool)
    @report_ui_errors
    def moveSubtitlePlacement(
        self,
        placement_id: str,
        start_frame: int,
        pixels_per_frame: float,
        playhead_frame: int,
        snap_enabled: bool,
    ) -> None:
        self._session._require_writable()
        placement = self._session.binding.current.get_subtitle_placement(placement_id)
        self._require_unlocked_subtitle_track(placement.track_id)
        duration = placement.end_frame - placement.start_frame
        next_start = max(0, int(start_frame))
        if snap_enabled:
            targets = self._session._timeline_snap_targets(
                [],
                playhead_frame,
                excluded_subtitle_placement_ids=[placement_id],
            )
            tolerance = self._session._snap_tolerance_frames(pixels_per_frame)
            adjustments = [
                target - edge
                for target in targets
                for edge in (next_start, next_start + duration)
                if abs(target - edge) <= tolerance
            ]
            if adjustments:
                next_start = max(0, next_start + min(adjustments, key=lambda value: abs(value)))
        self._session.binding.current.update_subtitle_placement_range(
            placement_id,
            start_frame=next_start,
            end_frame=next_start + duration,
        )
        self._finish_subtitle_placement_edit(placement_id, "已移动序列字幕")

    @Slot(str, int, int, float, int, bool)
    @report_ui_errors
    def resizeSubtitlePlacement(
        self,
        placement_id: str,
        start_frame: int,
        end_frame: int,
        pixels_per_frame: float,
        playhead_frame: int,
        snap_enabled: bool,
    ) -> None:
        self._session._require_writable()
        placement = self._session.binding.current.get_subtitle_placement(placement_id)
        self._require_unlocked_subtitle_track(placement.track_id)
        next_start = max(0, int(start_frame))
        next_end = max(next_start + 1, int(end_frame))
        if snap_enabled:
            targets = self._session._timeline_snap_targets(
                [],
                playhead_frame,
                excluded_subtitle_placement_ids=[placement_id],
            )
            tolerance = self._session._snap_tolerance_frames(pixels_per_frame)
            if next_start != placement.start_frame:
                next_start = self._session.binding.timeline.snap_frame(next_start, targets, tolerance)
                next_start = max(0, min(next_start, next_end - 1))
            if next_end != placement.end_frame:
                next_end = self._session.binding.timeline.snap_frame(next_end, targets, tolerance)
                next_end = max(next_start + 1, next_end)
        self._session.binding.current.update_subtitle_placement_range(
            placement_id,
            start_frame=next_start,
            end_frame=next_end,
        )
        self._finish_subtitle_placement_edit(placement_id, "已调整序列字幕时间")

    @Slot(str)
    @report_ui_errors
    def resetSubtitlePlacementTiming(self, placement_id: str) -> None:
        self._session._require_writable()
        placement = self._session.binding.current.get_subtitle_placement(placement_id)
        self._require_unlocked_subtitle_track(placement.track_id)
        self._session.binding.current.reset_subtitle_placement_range(placement_id)
        self._finish_subtitle_placement_edit(placement_id, "已恢复字幕文档时间")

    @Slot(str, str, str, int)
    @report_ui_errors
    def transcribeCurrentSequence(
        self,
        model: str,
        device: str,
        language: str,
        parallel_chunks: int,
    ) -> None:
        self._session._require_writable()
        selected_asr = AsrSettings.model_validate(
            {
                **self._session.settings.asr.model_dump(mode="python"),
                "model": model.strip(),
                "device": device,
                "language": language.strip() or "auto",
                "parallel_chunks": parallel_chunks,
            }
        )
        if not selected_asr.model:
            raise ValueError("请选择转录模型")
        candidate = self._session.settings.model_copy(deep=True)
        candidate.asr = selected_asr.model_copy(deep=True)
        self._session._commit_settings(candidate, "转录设置已更新")
        plan = self._current_transcription_plan(selected_asr)
        if plan.region_count <= 0:
            raise ValueError("请指定主要对白轨，并确认当前范围内有对白素材")
        self._session.tasks.start(
            TranscribeSequenceCommand(plan=plan),
            [source.asset_id for source in plan.sources],
            sequence_id=plan.sequence_id,
        )

    def _current_transcription_plan(
        self,
        asr: AsrSettings | None = None,
    ) -> TranscriptionPlan:
        if (
            not self._session.binding.current
            or not self._session.binding.timeline
            or not self._session.binding.active_sequence_id
        ):
            raise RuntimeError("当前没有可转录的时间轴")
        state = self._session.binding.timeline.state
        duration = state.duration_frames
        if duration <= 0:
            raise ValueError("当前时间轴还没有可转录的素材")
        bounds = state.sequence.in_out
        start_frame = min(duration, bounds.in_frame) if bounds else 0
        end_frame = min(duration, bounds.out_frame) if bounds else duration
        project = self._session.binding.current.get_project()
        project_profile = self._session.binding.current.get_sequence(
            project.main_sequence_id
        ).profile
        return build_dialogue_transcription_plan(
            state,
            {asset.id: asset for asset in self._session.binding.current.list_assets()},
            asr or self._session.settings.asr,
            project_profile=project_profile,
            start_frame=start_frame,
            end_frame=end_frame,
        )

    @Slot(str, str, str)
    @report_ui_errors
    def translateDocument(self, document_id: str, target_language: str, mode: str) -> None:
        self._session._require_writable()
        if not document_id:
            raise ValueError("请先选择源字幕文档")
        language = target_language.strip() or self._session.settings.translation.target_language
        if mode not in {"standard", "intelligent", "proofread"}:
            raise ValueError("请选择有效的翻译模式")
        self._session.tasks.start(
            TranslateDocumentCommand(
                document_id=document_id,
                target_language=language or "und",
                mode=mode,
            ),
        )

    @Slot()
    @report_ui_errors
    def translateSelectedSubtitleSegments(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        if not self._session.selection.subtitle_segment_ids:
            raise ValueError("请先选择要翻译的字幕段")
        mode = self._session.settings.translation.mode
        language = self._session.settings.translation.target_language
        self._session.tasks.start(
            TranslateSegmentsCommand(
                document_id=self._session.selection.document_id,
                segment_ids=list(self._session.selection.subtitle_segment_ids),
                target_language=language or "und",
                mode=mode,
            ),
        )

    @Slot(str, str, "QVariantList", str, str)
    @report_ui_errors
    def translateComparisonSegments(
        self,
        source_document_id: str,
        target_document_id: str,
        segment_ids: list[str],
        target_language: str,
        mode: str,
    ) -> None:
        self._session._require_writable()
        if not source_document_id or not target_document_id:
            raise ValueError("请先完成整篇翻译，再局部重译")
        if not segment_ids:
            raise ValueError("请先选择要重译的字幕段")
        self._session.tasks.start(
            TranslateSegmentsCommand(
                document_id=source_document_id,
                target_document_id=target_document_id,
                segment_ids=[str(item) for item in segment_ids],
                target_language=target_language or "und",
                mode=mode,
            )
        )

    @Slot(str, str, result="QVariantMap")
    def translationComparison(self, document_id: str, target_language: str) -> dict:
        if not document_id or not self._session.binding.current:
            return {}
        selected = self._session.binding.current.get_subtitle_document(document_id)
        documents = self._session.binding.current.list_subtitle_documents()
        if selected.source_document_id:
            source = self._session.binding.current.get_subtitle_document(selected.source_document_id)
            target = selected
        else:
            source = selected
            candidates = [item for item in documents if item.source_document_id == source.id]
            preferred = [item for item in candidates if item.language == target_language]
            target = (preferred or candidates)[-1] if (preferred or candidates) else None
        source_segments = self._session.binding.current.list_subtitle_segments(source.id)
        target_segments = (
            self._session.binding.current.list_subtitle_segments(target.id) if target is not None else []
        )
        rows: list[dict] = []
        matched_source_ids: set[str] = set()
        if target_segments and all(segment.source_segment_id for segment in target_segments):
            target_by_source = {segment.source_segment_id: segment for segment in target_segments}
            for source_segment in source_segments:
                translated = target_by_source.get(source_segment.id)
                if translated is not None:
                    matched_source_ids.add(source_segment.id)
                rows.append(
                    self._translation_comparison_row(
                        source_segments=[source_segment],
                        target_segment=translated,
                    )
                )
        else:
            for translated in target_segments:
                overlapping = [
                    source_segment
                    for source_segment in source_segments
                    if source_segment.end_frame > translated.start_frame
                    and source_segment.start_frame < translated.end_frame
                ]
                matched_source_ids.update(item.id for item in overlapping)
                rows.append(
                    self._translation_comparison_row(
                        source_segments=overlapping,
                        target_segment=translated,
                    )
                )
            for source_segment in source_segments:
                if source_segment.id not in matched_source_ids:
                    rows.append(
                        self._translation_comparison_row(
                            source_segments=[source_segment],
                            target_segment=None,
                        )
                    )
            rows.sort(key=lambda row: (row["startFrame"], row["endFrame"], row["rowId"]))
        source_text = "\n".join(segment.text for segment in source_segments).casefold()
        glossary_hits = sum(
            1
            for term in self._session.settings.translation.glossary_terms
            if term.source.casefold() in source_text
        )
        return {
            "sourceDocumentId": source.id,
            "targetDocumentId": target.id if target is not None else "",
            "sourceLanguage": source.language,
            "targetLanguage": target.language if target is not None else target_language,
            "glossaryHitCount": glossary_hits,
            "rows": rows,
        }

    @Slot(str, str, str, result=bool)
    @report_ui_errors
    def updateTranslationSegment(
        self,
        target_document_id: str,
        target_segment_id: str,
        text: str,
    ) -> bool:
        self._session._require_writable()
        segment = next(
            item
            for item in self._session.binding.current.list_subtitle_segments(target_document_id)
            if item.id == target_segment_id
        )
        self._session.binding.current.update_subtitle_segment(
            target_document_id,
            target_segment_id,
            start_frame=segment.start_frame,
            end_frame=segment.end_frame,
            text=text,
        )
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session._set_status("译文已保存")
        self._session.events.projectStateChanged.emit()
        self._session.events.historyChanged.emit()
        return True

    @Slot(str)
    @report_ui_errors
    def placeSubtitleDocument(self, document_id: str) -> None:
        self._session._require_writable()
        subtitle_track = next(
            (
                track
                for track in self._session.binding.timeline.state.tracks
                if track.kind == TrackKind.SUBTITLE and not track.locked
            ),
            None,
        )
        if subtitle_track is None:
            subtitle_track = self._session.binding.timeline.add_track(TrackKind.SUBTITLE)
        document = self._session.binding.current.get_subtitle_document(document_id)
        media_asset_id = document.media_asset_id or document.asset_id
        matching_clips = [
            clip for clip in self._session.binding.timeline.state.clips if clip.asset_id == media_asset_id
        ]
        if matching_clips:
            placements = self._session.binding.current.place_subtitle_document(
                document_id,
                subtitle_track.id,
                follow_clips=True,
            )
        else:
            placements = self._session.binding.current.place_subtitle_document(document_id, subtitle_track.id)
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.events.projectStateChanged.emit()
        self._session._set_status(f"已放入 {len(placements)} 条字幕")

    @Slot(str, str, bool)
    @report_ui_errors
    def updateSubtitlePlacementText(
        self,
        placement_id: str,
        text: str,
        apply_to_document: bool,
    ) -> None:
        self._session._require_writable()
        if apply_to_document:
            self._session.binding.current.apply_subtitle_placement_to_document(placement_id, text)
            self._session.projectors.subtitles.refresh_documents()
            self._session._set_status("修改已应用到字幕文档")
        else:
            self._session.binding.current.update_subtitle_placement_text(placement_id, text)
            self._session._set_status("已保存序列字幕覆盖")
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.events.selectionChanged.emit()
        self._session.projectors.timeline.schedule_preview_graph()

    @Slot(str, int, int, str, result=bool)
    @report_ui_errors
    def updateSubtitleSegment(
        self,
        segment_id: str,
        start_frame: int,
        end_frame: int,
        text: str,
    ) -> bool:
        self._session._require_writable()
        self._session._require_subtitle_document()
        segment = self._session.binding.current.update_subtitle_segment(
            self._session.selection.document_id,
            segment_id,
            start_frame=start_frame,
            end_frame=end_frame,
            text=text,
        )
        self._session._finish_subtitle_edit([segment.id], "字幕已保存")
        return True

    @Slot()
    @report_ui_errors
    def addSubtitleSegment(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        segments = self._session.binding.current.list_subtitle_segments(self._session.selection.document_id)
        selected = next(
            (item for item in segments if item.id == self.selectedSubtitleSegmentId),
            None,
        )
        start_frame = selected.end_frame if selected else (segments[-1].end_frame if segments else 0)
        profile = self._session.binding.current.get_sequence(
            self._session.binding.current.get_project().main_sequence_id
        ).profile
        segment = self._session.binding.current.add_subtitle_segment(
            self._session.selection.document_id,
            start_frame=start_frame,
            end_frame=start_frame + max(1, round(profile.fps * 2)),
            text="新字幕",
        )
        self._session._finish_subtitle_edit([segment.id], "已添加字幕")

    @Slot()
    @report_ui_errors
    def deleteSelectedSubtitleSegments(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.binding.current.delete_subtitle_segments(
            self._session.selection.document_id,
            self._session.selection.subtitle_segment_ids,
        )
        self._session._finish_subtitle_edit([], f"已删除 {count} 条字幕")

    @Slot()
    @report_ui_errors
    def mergeSelectedSubtitleSegments(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        merged = self._session.binding.current.merge_subtitle_segments(
            self._session.selection.document_id,
            self._session.selection.subtitle_segment_ids,
        )
        self._session._finish_subtitle_edit([merged.id], "字幕已合并")

    @Slot(str, int)
    @report_ui_errors
    def splitSubtitleSegment(self, segment_id: str, split_frame: int) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        first, second = self._session.binding.current.split_subtitle_segment(
            self._session.selection.document_id,
            segment_id,
            split_frame=None if split_frame < 0 else split_frame,
        )
        self._session._finish_subtitle_edit([first.id, second.id], "字幕已拆分")

    @Slot(int)
    @report_ui_errors
    def smartSplitSubtitleDocument(self, text_limit: int) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.binding.current.smart_split_subtitle_document(
            self._session.selection.document_id,
            text_limit=text_limit,
        )
        self._session._finish_subtitle_edit([], f"智能拆分完成，共拆分 {count} 条")

    @Slot()
    @report_ui_errors
    def fixSubtitleOverlaps(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.binding.current.fix_subtitle_overlaps(self._session.selection.document_id)
        self._session._finish_subtitle_edit([], f"已修复 {count} 条重叠字幕")

    @Slot()
    @report_ui_errors
    def copySelectedSubtitleSegments(self) -> None:
        self._session._require_subtitle_document()
        text = self._session.binding.current.selected_subtitle_segments_srt(
            self._session.selection.document_id,
            self._session.selection.subtitle_segment_ids,
        )
        QGuiApplication.clipboard().setText(text)
        self._session._set_status(f"已复制 {len(self._session.selection.subtitle_segment_ids)} 条字幕")

    @Slot()
    @report_ui_errors
    def pasteReplaceSelectedSubtitleSegments(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.binding.current.replace_selected_subtitle_texts(
            self._session.selection.document_id,
            self._session.selection.subtitle_segment_ids,
            QGuiApplication.clipboard().text(),
        )
        self._session._finish_subtitle_edit(
            list(self._session.selection.subtitle_segment_ids),
            f"已替换 {count} 条字幕",
        )

    @Slot()
    @report_ui_errors
    def openSubtitleFolder(self) -> None:
        self._session._require_subtitle_document()
        output = self._session.binding.current.write_subtitle_srt(self._session.selection.document_id)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(output.parent))):
            raise RuntimeError("无法打开字幕所在文件夹")

    @Slot(str, str, bool)
    @report_ui_errors
    def replaceSubtitleText(self, search: str, replacement: str, match_case: bool) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.binding.current.replace_all_subtitle_text(
            self._session.selection.document_id,
            search,
            replacement,
            match_case=match_case,
        )
        self._session._finish_subtitle_edit([], f"已替换 {count} 处文本")

    @Slot(str, int, int, str, str, bool)
    @report_ui_errors
    def replaceSubtitleMatch(
        self,
        segment_id: str,
        start: int,
        end: int,
        search: str,
        replacement: str,
        match_case: bool,
    ) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        updated = self._session.binding.current.replace_subtitle_match(
            self._session.selection.document_id,
            segment_id,
            start,
            end,
            search,
            replacement,
            match_case=match_case,
        )
        self._session._finish_subtitle_edit([updated.id], "已替换当前匹配")

    @Slot(str, bool, result="QVariantList")
    def findSubtitleMatches(self, search: str, match_case: bool) -> list[dict]:
        try:
            self._session._require_subtitle_document()
            return self._session.binding.current.find_subtitle_matches(
                self._session.selection.document_id,
                search,
                match_case=match_case,
            )
        except Exception as error:
            self._session.events.errorOccurred.emit(str(error))
            return []

    @Slot(str, str)
    @report_ui_errors
    def exportSubtitleDocument(self, document_id: str, path_url: str) -> None:
        if not document_id:
            raise RuntimeError("请先选择字幕文档")
        destination = self._session._local_path(path_url)
        if destination.suffix.lower() != ".srt":
            destination = destination.with_suffix(".srt")
        output = self._session.binding.current.write_subtitle_srt(document_id, destination)
        self._session._set_status(f"字幕已导出到 {output}")

    @Slot(int, result=str)
    def subtitleTextAtFrame(self, frame: int) -> str:
        if not self._session.presentation.preview_subtitles:
            return ""
        index = (
            bisect_right(self._session.presentation.preview_subtitles, frame, key=lambda item: item[0]) - 1
        )
        if index >= 0:
            start, end, text = self._session.presentation.preview_subtitles[index]
            if start <= frame < end:
                return text
        return ""

    @Slot(str, int, result=str)
    def subtitleTextForTrackAtFrame(self, track_id: str, frame: int) -> str:
        subtitles = self._session.presentation.preview_subtitles_by_track.get(track_id, [])
        if not subtitles:
            return ""
        index = bisect_right(subtitles, frame, key=lambda item: item[0]) - 1
        if index >= 0:
            start, end, text = subtitles[index]
            if start <= frame < end:
                return text
        return ""

    def _select_subtitle_placement_context(self, placement_id: str) -> None:
        row_index = self._session.models.subtitle_placements.findRow("placementId", placement_id)
        row = self._session.models.subtitle_placements.get(row_index)
        if not row:
            return
        document_id = str(row.get("documentId") or "")
        segment_id = str(row.get("segmentId") or "")
        document_changed = document_id and document_id != self._session.selection.document_id
        self._session.selection.subtitle_placement_id = placement_id
        if document_changed:
            self._session.selection.document_id = document_id
            self._session.projectors.subtitles.refresh_segments()
        self._session.selection.subtitle_segment_ids = [segment_id] if segment_id else []
        self._session.events.selectionChanged.emit()

    def _require_unlocked_subtitle_track(self, track_id: str) -> None:
        track = next(
            (item for item in self._session.binding.timeline.state.tracks if item.id == track_id), None
        )
        if track is None:
            raise KeyError(track_id)
        if track.locked:
            raise ValueError("字幕轨道已锁定")

    def _finish_subtitle_placement_edit(self, placement_id: str, status: str) -> None:
        self._session.selection.subtitle_placement_id = placement_id
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._select_subtitle_placement_context(placement_id)
        self._session.projectors.timeline.schedule_preview_graph()
        self._session._set_status(status)
        self._session.events.historyChanged.emit()

    @staticmethod
    def _translation_comparison_row(
        *,
        source_segments: list[SubtitleSegment],
        target_segment: SubtitleSegment | None,
    ) -> dict:
        source_ids = [segment.id for segment in source_segments]
        start_frames = [segment.start_frame for segment in source_segments]
        end_frames = [segment.end_frame for segment in source_segments]
        if target_segment is not None:
            start_frames.append(target_segment.start_frame)
            end_frames.append(target_segment.end_frame)
        start_frame = min(start_frames) if start_frames else 0
        end_frame = max(end_frames) if end_frames else start_frame + 1
        return {
            "rowId": target_segment.id if target_segment is not None else source_ids[0],
            "sourceSegmentIds": source_ids,
            "sourceText": "\n".join(segment.text for segment in source_segments),
            "targetSegmentId": target_segment.id if target_segment is not None else "",
            "targetText": target_segment.text if target_segment is not None else "",
            "startFrame": start_frame,
            "endFrame": end_frame,
            "status": "translated" if target_segment is not None else "missing",
        }
