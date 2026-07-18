from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol

from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.project import Asset, AssetFingerprint, MediaMetadata, Project, ProjectProfile, Sequence
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitlePlacement, SubtitleSegment
from mediaflow.domain.tasks import Task
from mediaflow.domain.timeline import TimelineMarker, TimelineRange, TimelineState
from mediaflow.domain.workflows import WorkflowRun


class MediaProbeResult(Protocol):
    kind: AssetKind
    metadata: MediaMetadata
    suggested_profile: ProjectProfile | None


class MediaProbePort(Protocol):
    def probe(
        self,
        path: str | Path,
        *,
        timeline_profile: ProjectProfile | None = None,
    ) -> MediaProbeResult: ...


FingerprintFile = Callable[[Path], AssetFingerprint]


class JsonClient(Protocol):
    def complete_json(self, *, system: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class JsonClientFactory(Protocol):
    def __call__(self, provider: LlmProviderSettings) -> JsonClient: ...


class TranslationCachePort(Protocol):
    def get(self, request: dict[str, Any]) -> list[str] | None: ...
    def put(self, request: dict[str, Any], texts: list[str]) -> None: ...


class TaskStore(Protocol):
    project_dir: Path

    def create(self, task: Task) -> Task: ...
    def save(self, task: Task) -> Task: ...
    def get(self, task_id: str) -> Task: ...
    def list(self) -> list[Task]: ...
    def delete(self, task_id: str) -> None: ...
    def delete_terminal(self) -> list[Task]: ...
    def recover_interrupted(self) -> list[Task]: ...


class ProjectAccess(Protocol):
    """Project identity and unit-of-work boundary shared by application services."""

    project_dir: Path
    read_only: bool

    def transaction(self) -> AbstractContextManager[Any]: ...
    def get_project(self) -> Project: ...


class WorkflowDocuments(Protocol):
    def set_workflow_auto_continue(self, value: bool | None) -> Project: ...
    def save_workflow_run(self, run: WorkflowRun) -> WorkflowRun: ...
    def get_workflow_run(self, run_id: str) -> WorkflowRun: ...
    def list_workflow_runs(self, *, active_only: bool = False) -> list[WorkflowRun]: ...


class SequenceDocuments(Protocol):
    def list_sequences(self, *, include_archived: bool = False) -> list[Sequence]: ...
    def get_sequence(self, sequence_id: str) -> Sequence: ...
    def save_sequence_export_preset(
        self,
        sequence_id: str,
        preset: ExportPreset,
    ) -> Sequence: ...
    def create_short_sequence(
        self,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> Sequence: ...
    def archive_short_sequence(self, sequence_id: str) -> Sequence: ...
    def restore_short_sequence(self, sequence_id: str) -> Sequence: ...


class AssetDocuments(Protocol):
    def add_asset(self, asset: Asset) -> Asset: ...
    def import_external_asset(self, path: str | Path, kind: AssetKind) -> Asset: ...
    def get_asset(self, asset_id: str) -> Asset: ...
    def list_assets(self) -> list[Asset]: ...
    def update_asset(self, asset: Asset) -> Asset: ...
    def refresh_asset_status(self, asset_id: str) -> Asset: ...
    def relink_asset(
        self,
        asset_id: str,
        replacement: str | Path,
        *,
        allow_different_content: bool = False,
    ) -> Asset: ...
    def resolve_asset_path(self, asset: Asset) -> Path: ...


class TimelineDocuments(Protocol):
    def load_timeline(self, sequence_id: str) -> TimelineState: ...
    def list_timeline_markers(self, sequence_id: str) -> list[TimelineMarker]: ...
    def list_timeline_ranges(self, sequence_id: str) -> list[TimelineRange]: ...
    def save_timeline(self, state: TimelineState) -> None: ...
    def save_clip_changes(self, state: TimelineState, clip_ids: set[str]) -> None: ...
    def apply_main_profile_change(
        self,
        state: TimelineState,
        assets: list[Asset],
        *,
        old_profile: ProjectProfile,
    ) -> None: ...


class AudioDocuments(Protocol):
    def list_audio_buses(self, sequence_id: str) -> list[AudioBus]: ...
    def save_audio_bus(self, bus: AudioBus) -> AudioBus: ...
    def save_audio_effect(self, effect: AudioEffect) -> AudioEffect: ...
    def list_audio_effects(self, bus_id: str) -> list[AudioEffect]: ...
    def save_audio_effect_chain(
        self,
        bus_id: str,
        effects: list[AudioEffect],
    ) -> list[AudioEffect]: ...
    def remove_audio_effect(self, effect_id: str) -> None: ...


class SubtitleDocuments(Protocol):
    def create_subtitle_document(
        self,
        document: SubtitleDocument,
        segments: list[SubtitleSegment],
    ) -> SubtitleDocument: ...
    def get_subtitle_document(self, document_id: str) -> SubtitleDocument: ...
    def list_subtitle_documents(
        self,
        asset_id: str | None = None,
    ) -> list[SubtitleDocument]: ...
    def list_subtitle_segments(self, document_id: str) -> list[SubtitleSegment]: ...
    def save_subtitle_segments(
        self,
        document_id: str,
        segments: list[SubtitleSegment],
    ) -> None: ...
    def place_subtitle_document(
        self,
        document_id: str,
        track_id: str,
        *,
        offset_frames: int = 0,
        source_start_frame: int | None = None,
        source_end_frame: int | None = None,
        follow_clips: bool | None = None,
    ) -> list[SubtitlePlacement]: ...
    def list_subtitle_placements(self, track_id: str) -> list[SubtitlePlacement]: ...
    def update_subtitle_placement_text(
        self,
        placement_id: str,
        text_override: str | None,
    ) -> SubtitlePlacement: ...
    def add_subtitle_placements(
        self,
        placements: list[SubtitlePlacement],
    ) -> list[SubtitlePlacement]: ...
    def apply_subtitle_placement_to_document(
        self,
        placement_id: str,
        text: str,
    ) -> SubtitleSegment: ...


class HighlightDocuments(Protocol):
    def save_highlights(self, candidates: list[HighlightCandidate]) -> None: ...
    def list_highlights(
        self,
        asset_id: str | None = None,
    ) -> list[HighlightCandidate]: ...
    def delete_highlight(self, candidate_id: str) -> None: ...


class AssetServiceDocuments(
    ProjectAccess,
    SequenceDocuments,
    AssetDocuments,
    TimelineDocuments,
    Protocol,
):
    pass


class HighlightServiceDocuments(
    ProjectAccess,
    SequenceDocuments,
    AssetDocuments,
    TimelineDocuments,
    SubtitleDocuments,
    HighlightDocuments,
    Protocol,
):
    pass


class SequenceServiceDocuments(
    ProjectAccess,
    SequenceDocuments,
    TimelineDocuments,
    AudioDocuments,
    SubtitleDocuments,
    Protocol,
):
    pass


class SubtitleAcquisitionDocuments(
    ProjectAccess,
    SequenceDocuments,
    AssetDocuments,
    SubtitleDocuments,
    Protocol,
):
    pass


class SubtitleEditingDocuments(
    ProjectAccess,
    SequenceDocuments,
    SubtitleDocuments,
    Protocol,
):
    pass


class SubtitlePublicationDocuments(
    ProjectAccess,
    SequenceDocuments,
    SubtitleDocuments,
    Protocol,
):
    pass


class TranslationDocuments(ProjectAccess, SubtitleDocuments, Protocol):
    pass


class WorkflowCoordinatorDocuments(ProjectAccess, WorkflowDocuments, Protocol):
    pass


class ProjectWorkflowDocuments(
    ProjectAccess,
    WorkflowDocuments,
    AssetDocuments,
    SubtitleDocuments,
    HighlightDocuments,
    Protocol,
):
    pass


class TimelineEditorDocuments(
    AssetDocuments,
    TimelineDocuments,
    AudioDocuments,
    Protocol,
):
    pass


class AssetProcessingDocuments(ProjectAccess, AssetDocuments, Protocol):
    pass


class TimelineCompilationDocuments(
    ProjectAccess,
    AssetDocuments,
    SubtitleDocuments,
    AudioDocuments,
    Protocol,
):
    pass


class TaskHandlerDocuments(
    ProjectAccess,
    SequenceDocuments,
    AssetDocuments,
    TimelineDocuments,
    SubtitleDocuments,
    HighlightDocuments,
    Protocol,
):
    pass
