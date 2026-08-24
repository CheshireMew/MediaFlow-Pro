from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mediaflow.application.asset_service import AssetService
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.subtitle_editing import _UNSET, SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_clock import asset_in_timeline_clock
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.transcript_editing import TranscriptEditingService
from mediaflow.application.translation_comparison import TranslationComparisonService
from mediaflow.domain.enums import AssetKind, AssetOrigin
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.project import Asset, ProjectProfile, Sequence
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.subtitles import SubtitlePlacement, SubtitleSegment
from mediaflow.domain.transcript_edits import (
    TranscriptEditPlan,
    TranscriptEditRequest,
    TranscriptEditResult,
    TranscriptSnapshot,
)
from mediaflow.domain.translation import TranslationComparison
from mediaflow.infrastructure.media_thumbnail_service import MediaThumbnailService
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths


class EditorProjectMediaCommands:
    _repository: ProjectRepository
    _paths: RuntimePaths
    _assets: AssetService
    _subtitle_editing: SubtitleEditingService
    _subtitle_publication: SubtitlePublicationService
    _transcript_editing: TranscriptEditingService
    _highlights: HighlightService
    _sequences: SequenceService
    _settings: ServiceSettings
    if TYPE_CHECKING:
        def _require_writable(self) -> None: ...
        def timeline(self, sequence_id: str) -> TimelineEditor: ...

    def import_external_asset(
        self,
        source: str | Path,
        *,
        expected_kind: AssetKind | None = None,
    ) -> Asset:
        return self._assets.import_external(source, expected_kind=expected_kind)

    def import_lut_asset(self, source: str | Path) -> Asset:
        self._require_writable()
        return self._assets.import_lut(source)

    def capture_asset_frame(self, asset_id: str, frame: int, sequence_id: str) -> Asset:
        self._require_writable()
        asset = self._repository.assets.get_asset(asset_id)
        sequence = self._repository.sequences.get_sequence(sequence_id)
        asset = asset_in_timeline_clock(
            self._repository.projects,
            self._repository.sequences,
            asset,
            sequence,
        )
        path = MediaThumbnailService(self._paths).capture_frame(
            self._repository,
            asset,
            frame=max(0, int(frame)),
            profile=sequence.profile,
        )
        return self._assets.register_output(path, AssetOrigin.GENERATED)

    def relink_asset(
        self,
        asset_id: str,
        replacement: str | Path,
        *,
        allow_different_content: bool = False,
    ) -> Asset:
        return self._assets.relink(
            asset_id,
            replacement,
            allow_different_content=allow_different_content,
        )

    def relink_offline_assets(self, directory: str | Path) -> tuple[list[Asset], list[Asset]]:
        return self._assets.relink_offline_from_directory(directory)

    def translation_comparison(
        self,
        document_id: str,
        target_language: str,
    ) -> TranslationComparison:
        return TranslationComparisonService(self._repository).compare(
            document_id,
            target_language,
            list(self._settings.translation.glossary_terms),
        )

    def update_translation_segment_text(
        self,
        document_id: str,
        segment_id: str,
        text: str,
    ) -> SubtitleSegment:
        segment = next(
            item
            for item in self._repository.subtitles.list_subtitle_segments(document_id)
            if item.id == segment_id
        )
        return self._subtitle_editing.update_segment(
            document_id,
            segment_id,
            start_frame=segment.start_frame,
            end_frame=segment.end_frame,
            text=text,
        )

    def suggested_profile(self, asset_id: str) -> ProjectProfile | None:
        return self._assets.suggested_profile(asset_id)

    def adopt_main_profile_from_video(self, asset_id: str) -> Asset:
        return self._assets.adopt_main_profile_from_video(asset_id)

    def update_subtitle_segment(
        self,
        document_id: str,
        segment_id: str,
        *,
        start_frame: int,
        end_frame: int,
        text: str,
        speaker: str | None | object = _UNSET,
    ) -> SubtitleSegment:
        return self._subtitle_editing.update_segment(
            document_id,
            segment_id,
            start_frame=start_frame,
            end_frame=end_frame,
            text=text,
            speaker=speaker,
        )

    def update_script_segment(
        self,
        document_id: str,
        segment_id: str,
        *,
        text: str | None = None,
        speaker: str | None | object = _UNSET,
    ) -> SubtitleSegment:
        return self._subtitle_editing.update_script_segment(
            document_id,
            segment_id,
            text=text,
            speaker=speaker,
        )

    def add_subtitle_segment(
        self,
        document_id: str,
        *,
        start_frame: int,
        end_frame: int,
        text: str,
    ) -> SubtitleSegment:
        return self._subtitle_editing.add_segment(
            document_id,
            start_frame=start_frame,
            end_frame=end_frame,
            text=text,
        )

    def delete_subtitle_segments(self, document_id: str, segment_ids: list[str]) -> int:
        return self._subtitle_editing.delete_segments(document_id, segment_ids)

    def merge_subtitle_segments(
        self,
        document_id: str,
        segment_ids: list[str],
    ) -> SubtitleSegment:
        return self._subtitle_editing.merge_segments(document_id, segment_ids)

    def split_subtitle_segment(
        self,
        document_id: str,
        segment_id: str,
        *,
        split_frame: int | None = None,
        split_index: int | None = None,
    ) -> tuple[SubtitleSegment, SubtitleSegment]:
        return self._subtitle_editing.split_segment(
            document_id,
            segment_id,
            split_frame=split_frame,
            split_index=split_index,
        )

    def smart_split_subtitle_document(self, document_id: str, *, text_limit: int = 24) -> int:
        return self._subtitle_editing.smart_split_document(document_id, text_limit=text_limit)

    def fix_subtitle_overlaps(self, document_id: str) -> int:
        return self._subtitle_editing.fix_overlaps(document_id)

    def selected_subtitle_segments_srt(self, document_id: str, segment_ids: list[str]) -> str:
        return self._subtitle_editing.selected_segments_srt(document_id, segment_ids)

    def replace_selected_subtitle_texts(
        self,
        document_id: str,
        segment_ids: list[str],
        clipboard_text: str,
    ) -> int:
        return self._subtitle_editing.replace_selected_texts(
            document_id,
            segment_ids,
            clipboard_text,
        )

    def replace_all_subtitle_text(
        self,
        document_id: str,
        search: str,
        replacement: str,
        *,
        match_case: bool = False,
    ) -> int:
        return self._subtitle_editing.replace_all(
            document_id,
            search,
            replacement,
            match_case=match_case,
        )

    def replace_subtitle_match(
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
        return self._subtitle_editing.replace_match(
            document_id,
            segment_id,
            start,
            end,
            search,
            replacement,
            match_case=match_case,
        )

    def find_subtitle_matches(
        self,
        document_id: str,
        search: str,
        *,
        match_case: bool = False,
    ) -> list[dict[str, int | str]]:
        return self._subtitle_editing.find_matches(
            document_id,
            search,
            match_case=match_case,
        )

    def update_subtitle_placement_range(
        self,
        placement_id: str,
        *,
        start_frame: int,
        end_frame: int,
    ) -> SubtitlePlacement:
        return self._subtitle_editing.update_placement_range(
            placement_id,
            start_frame=start_frame,
            end_frame=end_frame,
        )

    def reset_subtitle_placement_range(self, placement_id: str) -> SubtitlePlacement:
        return self._subtitle_editing.reset_placement_range(placement_id)

    def write_subtitle_srt(
        self,
        document_id: str,
        destination: str | Path | None = None,
    ) -> Path:
        return self._subtitle_publication.write_document_srt(document_id, destination)

    def inspect_transcript(
        self,
        sequence_id: str,
        *,
        document_id: str | None = None,
    ) -> TranscriptSnapshot:
        return self._transcript_editing.inspect_transcript(
            sequence_id,
            document_id=document_id,
        )

    def preview_transcript_edit(self, request: TranscriptEditRequest) -> TranscriptEditPlan:
        return self._transcript_editing.preview_plan(
            request,
            self.timeline(request.sequence_id),
        )

    def apply_transcript_edit(self, plan: TranscriptEditPlan) -> TranscriptEditResult:
        return self._transcript_editing.apply_plan(
            plan,
            self.timeline(plan.sequence_id),
        )

    def add_manual_highlight(
        self,
        asset_id: str,
        *,
        start_frame: int,
        end_frame: int,
        title: str | None = None,
        document_id: str | None = None,
    ) -> HighlightCandidate:
        return self._highlights.add_manual_candidate(
            asset_id,
            start_frame=start_frame,
            end_frame=end_frame,
            title=title,
            document_id=document_id,
        )

    def update_highlight(
        self,
        candidate_id: str,
        *,
        start_frame: int,
        end_frame: int,
        title: str,
    ) -> HighlightCandidate:
        return self._highlights.update_candidate(
            candidate_id,
            start_frame=start_frame,
            end_frame=end_frame,
            title=title,
        )

    def set_highlight_selected(
        self,
        candidate_id: str,
        selected: bool,
    ) -> HighlightCandidate:
        return self._highlights.set_selected(candidate_id, selected)

    def delete_highlight(self, candidate_id: str) -> None:
        self._highlights.delete_candidate(candidate_id)

    def create_highlight_short(
        self,
        candidate_id: str,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self._highlights.create_short_sequence(candidate_id, name=name)

    def selected_highlights(self, asset_id: str | None = None) -> list[HighlightCandidate]:
        return self._highlights.selected_candidates(asset_id)

    def create_short_from_range(
        self,
        source_sequence_id: str,
        range_id: str,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self._sequences.create_short_from_range(
            source_sequence_id,
            range_id,
            name=name,
        )

    def create_short_from_bounds(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self._sequences.create_short_from_bounds(
            source_sequence_id,
            start_frame,
            end_frame,
            name=name,
        )
