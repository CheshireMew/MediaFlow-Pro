from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaflow.application.dubbing_editing import DubbingEditingService
from mediaflow.application.web_package_files import web_package_root
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.domain.collaboration import ProjectChangeEvent
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.timeline import TimelineState
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

    def get_project(self):
        return self._repository.projects.get_project()

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

    def get_sequence(self, sequence_id: str):
        return self._repository.sequences.get_sequence(sequence_id)

    def list_sequences(self, *, include_archived: bool = False):
        return self._repository.sequences.list_sequences(include_archived=include_archived)

    def create_short_sequence(self, name: str, profile: ProjectProfile | None = None):
        return self._repository.sequences.create_short_sequence(name, profile)

    def get_asset(self, asset_id: str):
        return self._repository.assets.get_asset(asset_id)

    def list_assets(self):
        return self._repository.assets.list_assets()

    def list_asset_bins(self):
        return self._repository.assets.list_asset_bins()

    def create_asset_bin(self, name: str, parent_id: str | None = None):
        self._require_writable()
        return self._repository.assets.create_asset_bin(name, parent_id)

    def move_assets_to_bin(self, asset_ids: list[str], bin_id: str | None):
        self._require_writable()
        return self._repository.assets.move_assets_to_bin(asset_ids, bin_id)

    def resolve_asset_path(self, asset):
        return self._repository.assets.resolve_asset_path(asset)

    def load_timeline(self, sequence_id: str) -> TimelineState:
        return self._repository.timeline.load_timeline(sequence_id)

    def get_subtitle_document(self, document_id: str):
        return self._repository.subtitles.get_subtitle_document(document_id)

    def list_subtitle_documents(
        self,
        asset_id: str | None = None,
        *,
        sequence_id: str | None = None,
    ):
        return self._repository.subtitles.list_subtitle_documents(
            asset_id,
            sequence_id=sequence_id,
        )

    def list_subtitle_segments(self, document_id: str):
        return self._repository.subtitles.list_subtitle_segments(document_id)

    def list_subtitle_words(self, document_id: str, *, include_excluded: bool = True):
        return self._repository.subtitles.list_subtitle_words(
            document_id,
            include_excluded=include_excluded,
        )

    def subtitle_segment_summary(self, document_id: str) -> tuple[int, int, int]:
        return self._repository.subtitles.subtitle_segment_summary(document_id)

    def get_dubbing_session(self, session_id: str):
        return self._dubbing.get_session(session_id)

    def list_dubbing_sessions(self, *, sequence_id: str | None = None):
        return self._dubbing.list_sessions(sequence_id=sequence_id)

    def update_dubbing_speaker(self, *args: Any, **kwargs: Any):
        return self._dubbing.update_speaker(*args, **kwargs)

    def update_dubbing_reference(self, *args: Any, **kwargs: Any):
        return self._dubbing.update_reference(*args, **kwargs)

    def update_dubbing_utterance(self, *args: Any, **kwargs: Any):
        return self._dubbing.update_utterance(*args, **kwargs)

    def place_subtitle_document(self, *args: Any, **kwargs: Any):
        return self._repository.subtitles.place_subtitle_document(*args, **kwargs)

    def list_subtitle_placements(self, track_id: str):
        return self._repository.subtitles.list_subtitle_placements(track_id)

    def get_subtitle_placement(self, placement_id: str):
        return self._repository.subtitles.get_subtitle_placement(placement_id)

    def update_subtitle_placement_text(self, placement_id: str, text_override: str | None):
        return self._repository.subtitles.update_subtitle_placement_text(
            placement_id,
            text_override,
        )

    def apply_subtitle_placement_to_document(self, placement_id: str, text: str):
        return self._repository.subtitles.apply_subtitle_placement_to_document(
            placement_id,
            text,
        )

    def get_web_asset_spec(self, asset_id: str):
        return self._web_packages.inspect_asset(asset_id)

    def list_web_assets(self):
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

    def list_audio_buses(self, sequence_id: str):
        return self._repository.audio.list_audio_buses(sequence_id)

    def save_audio_bus(self, bus):
        return self._repository.audio.save_audio_bus(bus)

    def list_audio_effects(self, bus_id: str):
        return self._repository.audio.list_audio_effects(bus_id)

    def save_audio_effect(self, effect):
        return self._repository.audio.save_audio_effect(effect)

    def save_audio_effect_chain(self, bus_id: str, effects: list):
        return self._repository.audio.save_audio_effect_chain(bus_id, effects)

    def remove_audio_effect(self, effect_id: str) -> None:
        self._repository.audio.remove_audio_effect(effect_id)

    def list_export_history(self, sequence_id: str | None = None):
        return self._repository.records.list_export_history(sequence_id)

    def save_sequence_export_preset(self, sequence_id: str, preset):
        return self._repository.sequences.save_sequence_export_preset(sequence_id, preset)

    def list_highlights(self, asset_id: str | None = None):
        return self._repository.highlights.list_highlights(asset_id)

    def list_workflow_runs(self, *, active_only: bool = False):
        return self._repository.projects.list_workflow_runs(active_only=active_only)
