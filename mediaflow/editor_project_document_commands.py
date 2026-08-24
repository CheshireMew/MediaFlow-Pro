from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mediaflow.application.dubbing_editing import DubbingEditingService
from mediaflow.application.web_package_files import web_package_root
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.collaboration import ProjectChangeEvent
from mediaflow.domain.dubbing import DubbingSession
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.project import Asset, AssetBin, Project, ProjectProfile, Sequence
from mediaflow.domain.project_records import ExportHistoryRecord
from mediaflow.domain.subtitles import (
    SubtitleDocument,
    SubtitlePlacement,
    SubtitleSegment,
    SubtitleWord,
)
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_manifest import WebAssetSpec
from mediaflow.domain.workflows import WorkflowRun
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_browser import WebPackagePreviewServer
from mediaflow.infrastructure.web_render_target import WebRenderCache


class EditorProjectDocumentCommands:
    _repository: ProjectRepository
    _dubbing: DubbingEditingService
    _web_packages: WebPackageService
    _web_preview_server: WebPackagePreviewServer | None
    _web_preview_root: Path | None
    _paths: RuntimePaths

    if TYPE_CHECKING:

        def _require_writable(self) -> None: ...

    def get_project(self) -> Project:
        return self._repository.projects.get_project()

    def rename_project(self, name: str) -> Project:
        self._require_writable()
        return self._repository.projects.rename_project(name)

    def content_revision(self) -> int:
        return self._repository.content_revision()

    @property
    def owns_project_writer(self) -> bool:
        return self._repository.owns_project_lock and not self._repository.read_only

    def list_project_events(self, *, after_cursor: int = 0) -> list[ProjectChangeEvent]:
        return self._repository.events.list_events(after_cursor=after_cursor)

    def project_event_cursor(self) -> int:
        return self._repository.events.latest_cursor()

    def project_event_for_undo_group(
        self,
        undo_group_id: str,
    ) -> ProjectChangeEvent | None:
        return self._repository.events.for_undo_group(undo_group_id)

    def list_project_events_after_revision(self, revision: int) -> list[ProjectChangeEvent]:
        return self._repository.events.list_after_revision(revision)

    def has_pending_project_upgrade(self) -> bool:
        return self._repository.events.has_pending_upgrade()

    def get_sequence(self, sequence_id: str) -> Sequence:
        return self._repository.sequences.get_sequence(sequence_id)

    def list_sequences(self, *, include_archived: bool = False) -> list[Sequence]:
        return self._repository.sequences.list_sequences(include_archived=include_archived)

    def create_short_sequence(
        self,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> Sequence:
        return self._repository.sequences.create_short_sequence(name, profile)

    def get_asset(self, asset_id: str) -> Asset:
        return self._repository.assets.get_asset(asset_id)

    def list_assets(self) -> list[Asset]:
        return self._repository.assets.list_assets()

    def list_asset_bins(self) -> list[AssetBin]:
        return self._repository.assets.list_asset_bins()

    def create_asset_bin(self, name: str, parent_id: str | None = None) -> AssetBin:
        self._require_writable()
        return self._repository.assets.create_asset_bin(name, parent_id)

    def move_assets_to_bin(self, asset_ids: list[str], bin_id: str | None) -> list[Asset]:
        self._require_writable()
        return self._repository.assets.move_assets_to_bin(asset_ids, bin_id)

    def resolve_asset_path(self, asset: Asset) -> Path:
        return self._repository.assets.resolve_asset_path(asset)

    def load_timeline(self, sequence_id: str) -> TimelineState:
        return self._repository.timeline.load_timeline(sequence_id)

    def get_subtitle_document(self, document_id: str) -> SubtitleDocument:
        return self._repository.subtitles.get_subtitle_document(document_id)

    def list_subtitle_documents(
        self,
        asset_id: str | None = None,
        *,
        sequence_id: str | None = None,
    ) -> list[SubtitleDocument]:
        return self._repository.subtitles.list_subtitle_documents(
            asset_id,
            sequence_id=sequence_id,
        )

    def list_subtitle_segments(self, document_id: str) -> list[SubtitleSegment]:
        return self._repository.subtitles.list_subtitle_segments(document_id)

    def list_subtitle_words(
        self,
        document_id: str,
        *,
        include_excluded: bool = True,
    ) -> list[SubtitleWord]:
        return self._repository.subtitles.list_subtitle_words(
            document_id,
            include_excluded=include_excluded,
        )

    def subtitle_segment_summary(self, document_id: str) -> tuple[int, int, int]:
        return self._repository.subtitles.subtitle_segment_summary(document_id)

    def get_dubbing_session(self, session_id: str) -> DubbingSession:
        return self._dubbing.get_session(session_id)

    def list_dubbing_sessions(self, *, sequence_id: str | None = None) -> list[DubbingSession]:
        return self._dubbing.list_sessions(sequence_id=sequence_id)

    def update_dubbing_speaker(
        self,
        session_id: str,
        speaker_id: str,
        *,
        expected_revision: int,
        display_name: str,
        review_status: str,
        primary_reference_id: str,
    ) -> DubbingSession:
        return self._dubbing.update_speaker(
            session_id,
            speaker_id,
            expected_revision=expected_revision,
            display_name=display_name,
            review_status=review_status,
            primary_reference_id=primary_reference_id,
        )

    def update_dubbing_reference(
        self,
        session_id: str,
        speaker_id: str,
        reference_id: str,
        *,
        expected_revision: int,
        text: str,
        language: str,
    ) -> DubbingSession:
        return self._dubbing.update_reference(
            session_id,
            speaker_id,
            reference_id,
            expected_revision=expected_revision,
            text=text,
            language=language,
        )

    def update_dubbing_utterance(
        self,
        session_id: str,
        utterance_id: str,
        *,
        expected_revision: int,
        target_text: str,
        speaker_id: str,
        review_status: str,
    ) -> DubbingSession:
        return self._dubbing.update_utterance(
            session_id,
            utterance_id,
            expected_revision=expected_revision,
            target_text=target_text,
            speaker_id=speaker_id,
            review_status=review_status,
        )

    def place_subtitle_document(
        self,
        document_id: str,
        track_id: str,
        *,
        offset_frames: int = 0,
        source_start_frame: int | None = None,
        source_end_frame: int | None = None,
        follow_clips: bool | None = None,
    ) -> list[SubtitlePlacement]:
        return self._repository.subtitles.place_subtitle_document(
            document_id,
            track_id,
            offset_frames=offset_frames,
            source_start_frame=source_start_frame,
            source_end_frame=source_end_frame,
            follow_clips=follow_clips,
        )

    def list_subtitle_placements(self, track_id: str) -> list[SubtitlePlacement]:
        return self._repository.subtitles.list_subtitle_placements(track_id)

    def list_subtitle_placements_for_segments(
        self,
        sequence_id: str,
        segment_ids: list[str],
    ) -> list[SubtitlePlacement]:
        return self._repository.subtitles.list_subtitle_placements_for_segments(
            sequence_id,
            segment_ids,
        )

    def get_subtitle_placement(self, placement_id: str) -> SubtitlePlacement:
        return self._repository.subtitles.get_subtitle_placement(placement_id)

    def update_subtitle_placement_text(
        self,
        placement_id: str,
        text_override: str | None,
    ) -> SubtitlePlacement:
        return self._repository.subtitles.update_subtitle_placement_text(
            placement_id,
            text_override,
        )

    def apply_subtitle_placement_to_document(
        self,
        placement_id: str,
        text: str,
    ) -> SubtitleSegment:
        return self._repository.subtitles.apply_subtitle_placement_to_document(
            placement_id,
            text,
        )

    def get_web_asset_spec(self, asset_id: str) -> WebAssetSpec:
        return self._web_packages.inspect_asset(asset_id)

    def list_web_assets(self) -> list[WebAssetSpec]:
        return self._repository.web.list_web_asset_specs()

    def web_editor_entry_url(self, asset_id: str) -> str:
        asset = self._repository.assets.get_asset(asset_id)
        spec = self._web_packages.inspect_asset(asset_id)
        package_root = web_package_root(
            self._repository.assets.resolve_asset_path(asset),
            spec.manifest,
        )
        if self._web_preview_server is None or self._web_preview_root != package_root:
            self.close_web_preview()
            self._web_preview_server = WebPackagePreviewServer(package_root)
            self._web_preview_root = package_root
        return self._web_preview_server.url_for(
            spec.manifest.entry,
            query=(
                f"capture=1&variant={spec.manifest.default_variant_id}&scene={spec.manifest.scenes[0].id}"
            ),
        )

    def close_web_preview(self) -> None:
        if self._web_preview_server is not None:
            self._web_preview_server.close()
        self._web_preview_server = None
        self._web_preview_root = None

    def web_render_cache_ready(self, state: TimelineState, clip_id: str) -> bool:
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self._repository.assets.get_asset(clip.asset_id)
        return (
            WebRenderCache(
                self._repository,
                self._paths,
            )
            .target(state, clip, asset)
            .path.is_file()
        )

    def list_audio_buses(self, sequence_id: str) -> list[AudioBus]:
        return self._repository.audio.list_audio_buses(sequence_id)

    def save_audio_bus(self, bus: AudioBus) -> AudioBus:
        return self._repository.audio.save_audio_bus(bus)

    def list_audio_effects(self, bus_id: str) -> list[AudioEffect]:
        return self._repository.audio.list_audio_effects(bus_id)

    def save_audio_effect(self, effect: AudioEffect) -> AudioEffect:
        return self._repository.audio.save_audio_effect(effect)

    def save_audio_effect_chain(self, bus_id: str, effects: list[AudioEffect]) -> list[AudioEffect]:
        return self._repository.audio.save_audio_effect_chain(bus_id, effects)

    def remove_audio_effect(self, effect_id: str) -> None:
        self._repository.audio.remove_audio_effect(effect_id)

    def list_export_history(
        self,
        sequence_id: str | None = None,
    ) -> list[ExportHistoryRecord]:
        return self._repository.records.list_export_history(sequence_id)

    def save_sequence_export_preset(
        self,
        sequence_id: str,
        preset: ExportPreset,
    ) -> Sequence:
        return self._repository.sequences.save_sequence_export_preset(sequence_id, preset)

    def list_highlights(self, asset_id: str | None = None) -> list[HighlightCandidate]:
        return self._repository.highlights.list_highlights(asset_id)

    def list_workflow_runs(self, *, active_only: bool = False) -> list[WorkflowRun]:
        return self._repository.projects.list_workflow_runs(active_only=active_only)
