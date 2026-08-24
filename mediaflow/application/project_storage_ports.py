from __future__ import annotations

import builtins
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol

from mediaflow.application.events import TaskEvent
from mediaflow.domain.asr import AsrResult
from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.dubbing import DubbingSession
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.frame_clock import MainFrameClockSnapshot
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.project import Asset, AssetBin, AssetFingerprint, Project, ProjectProfile, Sequence
from mediaflow.domain.project_records import ExportHistoryRecord, ProjectVersionRecord
from mediaflow.domain.subtitles import SubtitleDocument, SubtitlePlacement, SubtitleSegment, SubtitleWord
from mediaflow.domain.tasks import Task, TaskStopRequest
from mediaflow.domain.timeline import TimelineMarker, TimelineRange, TimelineState
from mediaflow.domain.web_manifest import EditableMediaManifest, WebAssetSpec
from mediaflow.domain.web_state import WebClipState
from mediaflow.domain.workflows import WorkflowRun


class TaskStore(Protocol):
    project_dir: Path
    read_only: bool

    def create(self, task: Task, *, event_type: str = "created") -> Task: ...
    def claim(
        self,
        task_id: str,
        owner_id: str,
        lease_duration_ms: int,
    ) -> tuple[Task, bool] | None: ...
    def renew_lease(
        self,
        task_id: str,
        owner_id: str,
        lease_duration_ms: int,
    ) -> Task | None: ...
    def update_owned(
        self,
        task: Task,
        owner_id: str,
        *,
        event_type: str,
        release_owner: bool = False,
    ) -> Task | None: ...
    def queue_paused(self, task_id: str) -> Task | None: ...
    def request_stop(
        self,
        task_id: str,
        request: TaskStopRequest,
    ) -> Task | None: ...
    def get(self, task_id: str) -> Task: ...
    def list(self) -> builtins.list[Task]: ...
    def list_claimable(self, at_ms: int) -> builtins.list[Task]: ...
    def delete(self, task_id: str, *, event_type: str = "deleted") -> None: ...
    def delete_terminal(self) -> builtins.list[Task]: ...
    def snapshot(self) -> tuple[builtins.list[Task], int]: ...
    def latest_event(self, task_id: str) -> TaskEvent: ...
    def events_after(self, cursor: int, *, limit: int = 500) -> builtins.list[TaskEvent]: ...


class TaskProjectAccess(Protocol):
    project_dir: Path
    read_only: bool

    def transaction(self) -> AbstractContextManager[Any]: ...

    def task_transaction(self) -> AbstractContextManager[Any]: ...


class ProjectAccess(Protocol):
    """Project storage context and unit-of-work boundary."""

    project_dir: Path
    read_only: bool

    def transaction(self) -> AbstractContextManager[Any]: ...
    def enlist_transaction_publication(
        self,
        *,
        on_commit: Callable[[], None],
        on_rollback: Callable[[BaseException], None],
    ) -> None: ...
    def content_revision(self) -> int: ...


class ProjectRecordsDocuments(Protocol):
    def save_export_history(self, record: ExportHistoryRecord) -> ExportHistoryRecord: ...
    def list_export_history(self, sequence_id: str | None = None) -> list[ExportHistoryRecord]: ...
    def create_project_version(self, name: str) -> ProjectVersionRecord: ...
    def list_project_versions(self) -> list[ProjectVersionRecord]: ...
    def restore_project_version(self, version_id: str) -> ProjectVersionRecord: ...


class ProjectMetadataDocuments(Protocol):
    def get_project(self) -> Project: ...
    def rename_project(self, name: str) -> Project: ...


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
    def prepare_short_sequence(
        self,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> Sequence: ...
    def commit_short_sequence(self, sequence: Sequence) -> Sequence: ...
    def archive_short_sequence(self, sequence_id: str) -> Sequence: ...
    def restore_short_sequence(self, sequence_id: str) -> Sequence: ...


class AssetDocuments(Protocol):
    def resolve_existing_file(self, path: str | Path) -> Path: ...
    def is_regular_file(self, path: str | Path) -> bool: ...
    def files_by_size(
        self,
        directory: str | Path,
        expected_sizes: set[int],
    ) -> dict[int, list[Path]]: ...
    def add_asset(self, asset: Asset) -> Asset: ...
    def prepare_external_asset(
        self,
        path: str | Path,
        kind: AssetKind,
    ) -> Asset: ...
    def commit_external_asset(self, asset: Asset) -> Asset: ...
    def import_external_asset(self, path: str | Path, kind: AssetKind) -> Asset: ...
    def get_asset(self, asset_id: str) -> Asset: ...
    def list_assets(self) -> list[Asset]: ...
    def list_asset_bins(self) -> list[AssetBin]: ...
    def create_asset_bin(self, name: str, parent_id: str | None = None) -> AssetBin: ...
    def move_assets_to_bin(self, asset_ids: list[str], bin_id: str | None) -> list[Asset]: ...
    def update_asset(self, asset: Asset) -> Asset: ...
    def set_asset_proxy_paths(
        self,
        asset_id: str,
        *,
        expected_fingerprint: AssetFingerprint | None,
        proxy_path: str | Path,
        sdr_preview_proxy_path: str | Path | None,
    ) -> Asset: ...
    def set_asset_waveform_path(
        self,
        asset_id: str,
        *,
        expected_fingerprint: AssetFingerprint | None,
        waveform_path: str | Path,
    ) -> Asset: ...
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
    def save_timeline(self, state: TimelineState) -> int: ...
    def save_clip_changes(self, state: TimelineState, clip_ids: set[str]) -> int: ...
    def save_clip_set_changes(
        self,
        state: TimelineState,
        *,
        changed_clip_ids: set[str],
        removed_clip_ids: set[str],
        changed_web_state_ids: set[str],
    ) -> int: ...


class FrameClockDocuments(Protocol):
    def capture_main_frame_clock(self, sequence_id: str) -> MainFrameClockSnapshot: ...
    def change_main_frame_clock(
        self,
        source: MainFrameClockSnapshot,
        state: TimelineState,
        assets: list[Asset],
        *,
        old_profile: ProjectProfile,
    ) -> MainFrameClockSnapshot: ...
    def restore_main_frame_clock(
        self,
        source: MainFrameClockSnapshot,
        destination: MainFrameClockSnapshot,
    ) -> MainFrameClockSnapshot: ...


class WebMediaDocuments(Protocol):
    def save_web_asset_spec(self, spec: WebAssetSpec) -> WebAssetSpec: ...
    def get_web_asset_spec(self, asset_id: str) -> WebAssetSpec: ...
    def list_web_asset_specs(self) -> list[WebAssetSpec]: ...
    def get_web_clip_state(self, clip_id: str) -> WebClipState: ...
    def list_web_clip_states(self, sequence_id: str) -> dict[str, WebClipState]: ...
    def save_web_clip_states(self, states: list[WebClipState]) -> None: ...


class WebPackageValidatorPort(Protocol):
    def validate(self, package_root: Path, manifest: EditableMediaManifest) -> None: ...


class AudioDocuments(Protocol):
    def list_audio_buses(self, sequence_id: str) -> list[AudioBus]: ...
    def save_audio_bus(self, bus: AudioBus) -> AudioBus: ...
    def replace_audio_graph(
        self,
        sequence_id: str,
        buses: list[AudioBus],
        effects: list[AudioEffect],
    ) -> None: ...
    def save_audio_effect(self, effect: AudioEffect) -> AudioEffect: ...
    def list_audio_effects(self, bus_id: str) -> list[AudioEffect]: ...
    def save_audio_effect_chain(
        self,
        bus_id: str,
        effects: list[AudioEffect],
    ) -> list[AudioEffect]: ...
    def remove_audio_effect(self, effect_id: str) -> None: ...


class SubtitleDocuments(Protocol):
    def get_asset_transcript(
        self,
        asset_id: str,
        signature: str,
    ) -> AsrResult | None: ...
    def save_asset_transcript(
        self,
        asset_id: str,
        signature: str,
        result: AsrResult,
    ) -> AsrResult: ...
    def create_subtitle_document(
        self,
        document: SubtitleDocument,
        segments: list[SubtitleSegment],
        words: list[SubtitleWord] | None = None,
    ) -> SubtitleDocument: ...
    def save_subtitle_document(self, document: SubtitleDocument) -> SubtitleDocument: ...
    def get_subtitle_document(self, document_id: str) -> SubtitleDocument: ...
    def list_subtitle_documents(
        self,
        asset_id: str | None = None,
        *,
        sequence_id: str | None = None,
    ) -> list[SubtitleDocument]: ...
    def list_subtitle_segments(self, document_id: str) -> list[SubtitleSegment]: ...
    def get_subtitle_segment(
        self,
        document_id: str,
        segment_id: str,
    ) -> SubtitleSegment: ...
    def subtitle_segment_summary(self, document_id: str) -> tuple[int, int, int]: ...
    def save_subtitle_segments(
        self,
        document_id: str,
        segments: list[SubtitleSegment],
    ) -> None: ...
    def list_subtitle_words(
        self,
        document_id: str,
        *,
        include_excluded: bool = True,
    ) -> list[SubtitleWord]: ...
    def list_subtitle_words_for_segment(
        self,
        document_id: str,
        segment_id: str,
        *,
        include_excluded: bool = True,
    ) -> list[SubtitleWord]: ...
    def get_subtitle_word(
        self,
        document_id: str,
        word_id: str,
    ) -> SubtitleWord: ...
    def save_subtitle_segment_state(
        self,
        document_id: str,
        segment: SubtitleSegment,
        words: list[SubtitleWord],
    ) -> None: ...
    def save_subtitle_words(
        self,
        document_id: str,
        words: list[SubtitleWord],
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
    def list_subtitle_placements_for_segments(
        self,
        sequence_id: str,
        segment_ids: list[str],
    ) -> list[SubtitlePlacement]: ...
    def get_subtitle_placement(self, placement_id: str) -> SubtitlePlacement: ...
    def update_subtitle_placement_text(
        self,
        placement_id: str,
        text_override: str | None,
    ) -> SubtitlePlacement: ...
    def update_subtitle_placement_range(
        self,
        placement_id: str,
        start_frame: int,
        end_frame: int,
        *,
        timing_overridden: bool = True,
    ) -> SubtitlePlacement: ...
    def reset_subtitle_placement_range(self, placement_id: str) -> SubtitlePlacement: ...
    def add_subtitle_placements(
        self,
        placements: list[SubtitlePlacement],
    ) -> list[SubtitlePlacement]: ...
    def apply_subtitle_placement_to_document(
        self,
        placement_id: str,
        text: str,
    ) -> SubtitleSegment: ...


class DubbingDocuments(Protocol):
    def create_session(self, session: DubbingSession) -> DubbingSession: ...
    def save_session(
        self,
        session: DubbingSession,
        *,
        expected_revision: int,
    ) -> DubbingSession: ...
    def get_session(self, session_id: str) -> DubbingSession: ...
    def list_sessions(
        self,
        *,
        sequence_id: str | None = None,
    ) -> list[DubbingSession]: ...


class HighlightDocuments(Protocol):
    def save_highlights(self, candidates: list[HighlightCandidate]) -> None: ...
    def list_highlights(
        self,
        asset_id: str | None = None,
    ) -> list[HighlightCandidate]: ...
    def delete_highlight(self, candidate_id: str) -> None: ...
