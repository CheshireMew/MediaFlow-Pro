from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaflow.application.asset_service import AssetService
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.timeline_clock import asset_in_timeline_clock
from mediaflow.application.transcript_editing import TranscriptEditingService
from mediaflow.domain.enums import AssetOrigin
from mediaflow.domain.project import ProjectProfile
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
    if TYPE_CHECKING:
        def _require_writable(self) -> None: ...

    def import_external_asset(self, source: str | Path, *, expected_kind=None):
        return self._assets.import_external(source, expected_kind=expected_kind)

    def import_lut_asset(self, source: str | Path):
        self._require_writable()
        return self._assets.import_lut(source)

    def capture_asset_frame(self, asset_id: str, frame: int, sequence_id: str):
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
    ):
        return self._assets.relink(
            asset_id,
            replacement,
            allow_different_content=allow_different_content,
        )

    def relink_offline_assets(self, directory: str | Path):
        return self._assets.relink_offline_from_directory(directory)

    def suggested_profile(self, asset_id: str) -> ProjectProfile | None:
        return self._assets.suggested_profile(asset_id)

    def adopt_main_profile_from_video(self, asset_id: str):
        return self._assets.adopt_main_profile_from_video(asset_id)

    def update_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.update_segment(*args, **kwargs)

    def update_script_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.update_script_segment(*args, **kwargs)

    def add_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.add_segment(*args, **kwargs)

    def delete_subtitle_segments(self, document_id: str, segment_ids: list[str]) -> int:
        return self._subtitle_editing.delete_segments(document_id, segment_ids)

    def merge_subtitle_segments(self, document_id: str, segment_ids: list[str]):
        return self._subtitle_editing.merge_segments(document_id, segment_ids)

    def split_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.split_segment(*args, **kwargs)

    def smart_split_subtitle_document(self, document_id: str, *, text_limit: int = 24) -> int:
        return self._subtitle_editing.smart_split_document(document_id, text_limit=text_limit)

    def fix_subtitle_overlaps(self, document_id: str) -> int:
        return self._subtitle_editing.fix_overlaps(document_id)

    def selected_subtitle_segments_srt(self, document_id: str, segment_ids: list[str]) -> str:
        return self._subtitle_editing.selected_segments_srt(document_id, segment_ids)

    def replace_selected_subtitle_texts(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_selected_texts(*args, **kwargs)

    def replace_all_subtitle_text(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_all(*args, **kwargs)

    def replace_subtitle_match(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_match(*args, **kwargs)

    def find_subtitle_matches(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.find_matches(*args, **kwargs)

    def update_subtitle_placement_range(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.update_placement_range(*args, **kwargs)

    def reset_subtitle_placement_range(self, placement_id: str):
        return self._subtitle_editing.reset_placement_range(placement_id)

    def write_subtitle_srt(
        self,
        document_id: str,
        destination: str | Path | None = None,
    ) -> Path:
        return self._subtitle_publication.write_document_srt(document_id, destination)

    def inspect_transcript(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.inspect_transcript(*args, **kwargs)

    def preview_transcript_edit(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.preview_plan(*args, **kwargs)

    def apply_transcript_edit(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.apply_plan(*args, **kwargs)

    def add_manual_highlight(self, *args: Any, **kwargs: Any):
        return self._highlights.add_manual_candidate(*args, **kwargs)

    def update_highlight(self, *args: Any, **kwargs: Any):
        return self._highlights.update_candidate(*args, **kwargs)

    def set_highlight_selected(self, candidate_id: str, selected: bool):
        return self._highlights.set_selected(candidate_id, selected)

    def delete_highlight(self, candidate_id: str) -> None:
        self._highlights.delete_candidate(candidate_id)

    def create_highlight_short(self, candidate_id: str, *, name: str | None = None):
        return self._highlights.create_short_sequence(candidate_id, name=name)

    def selected_highlights(self, asset_id: str | None = None):
        return self._highlights.selected_candidates(asset_id)

    def create_short_from_range(self, *args: Any, **kwargs: Any):
        return self._sequences.create_short_from_range(*args, **kwargs)

    def create_short_from_bounds(self, *args: Any, **kwargs: Any):
        return self._sequences.create_short_from_bounds(*args, **kwargs)
