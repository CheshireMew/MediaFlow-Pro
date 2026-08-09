from __future__ import annotations

import threading
from concurrent.futures import Future
from dataclasses import dataclass, field

from PySide6.QtCore import QObject

from mediaflow.desktop.models import (
    AssetBinListModel,
    AssetFilterModel,
    AssetListModel,
    AssetMomentFilterModel,
    AssetMomentListModel,
    AudioBusListModel,
    AudioEffectListModel,
    AudioEffectParameterListModel,
    ClipListModel,
    CompoundClipListModel,
    DownloadEntryListModel,
    GlossaryTermListModel,
    HighlightListModel,
    LlmProviderListModel,
    RecentProjectListModel,
    SequenceListModel,
    SubtitleDocumentListModel,
    SubtitlePlacementListModel,
    SubtitleSegmentListModel,
    TaskListModel,
    TimelineMarkerListModel,
    TimelineRangeListModel,
    TrackListModel,
    TransitionListModel,
    WebLayerListModel,
)
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.tasks import Task
from mediaflow.service.desktop_proxy import RemoteEditorProject, RemoteTimelineEditor

DesktopProject = RemoteEditorProject
DesktopTimeline = RemoteTimelineEditor


@dataclass(frozen=True)
class TimelinePlacement:
    track_id: str = ""
    track_position: int | None = None
    start_frame: int | None = None
    pixels_per_frame: float = 3.0
    playhead_frame: int = 0
    snap_enabled: bool = True
    force_new_track: bool = False
    source_in_frame: int = 0
    source_out_frame: int | None = None


@dataclass(frozen=True)
class PlacedTimelineAsset:
    track_id: str
    end_frame: int


@dataclass
class ImportDropBatch:
    placement: TimelinePlacement
    asset_ids: list[str | None]
    pending_task_ids: set[str] = field(default_factory=set)


@dataclass(slots=True)
class ProjectBinding:
    current: DesktopProject | None = None
    timeline: DesktopTimeline | None = None
    project_id: str = ""
    active_sequence_id: str = ""
    generation: int = 0
    task_subscription_token: int | None = None
    project_subscription_token: int | None = None
    workspace_subscription_token: int | None = None


@dataclass(slots=True)
class SelectionState:
    asset_ids: list[str] = field(default_factory=list)
    clip_ids: list[str] = field(default_factory=list)
    compound_id: str = ""
    document_id: str = ""
    subtitle_segment_ids: list[str] = field(default_factory=list)
    subtitle_placement_id: str = ""
    glossary_term_id: str = ""
    llm_provider_id: str = ""
    highlight_id: str = ""
    audio_bus_id: str = ""
    audio_effect_id: str = ""
    transition_id: str = ""
    marker_id: str = ""
    range_id: str = ""
    watermark_asset_id: str = ""
    range_in_frame: int | None = None


@dataclass(slots=True)
class TaskViewState:
    cursor: int = 0
    revisions: dict[str, int] = field(default_factory=dict)
    items: dict[str, Task] = field(default_factory=dict)


@dataclass(slots=True)
class PresentationState:
    status_message: str = ""
    last_error_id: str = ""
    collaboration_conflict: dict = field(default_factory=dict)
    preview_graph_path: str = ""
    hdr_preview_active: bool = False
    preview_subtitles: list[tuple[int, int, str]] = field(default_factory=list)
    preview_subtitles_by_track: dict[
        str,
        list[tuple[int, int, str]],
    ] = field(default_factory=dict)
    audio_metrics: dict = field(default_factory=dict)
    encoder_policy_options: list[dict] = field(default_factory=list)
    home_summary: dict = field(
        default_factory=lambda: {
            "runningTaskCount": 0,
            "failedTaskCount": 0,
            "offlineAssetCount": 0,
            "pendingWorkflowCount": 0,
            "recentArtifactCount": 0,
        }
    )
    pending_preview_range: tuple[int, int] | None = None
    filmstrip_frames: dict[str, list[dict]] = field(default_factory=dict)


@dataclass(slots=True)
class AssetInteractionState:
    pending_profile_asset_id: str = ""
    pending_profile_label: str = ""
    pending_profile_placement: TimelinePlacement = field(default_factory=TimelinePlacement)
    pending_batch_ids: list[str] = field(default_factory=list)
    pending_batch_placement: TimelinePlacement = field(default_factory=TimelinePlacement)
    pending_import_tasks: dict[str, tuple[str, int]] = field(default_factory=dict)
    pending_import_batches: dict[str, ImportDropBatch] = field(default_factory=dict)
    pending_relink_asset_id: str = ""
    pending_relink_path: str = ""
    waveform_cache: dict[str, tuple[str, int, dict]] = field(default_factory=dict)
    waveform_pending: set[tuple[int, str, str]] = field(default_factory=set)
    thumbnail_paths: dict[str, str] = field(default_factory=dict)
    thumbnail_request_id: int = 0
    thumbnail_pending_request: tuple[int, int, str] | None = None
    thumbnail_refresh_requested: bool = False


@dataclass(slots=True)
class DownloadState:
    plan: DownloadPlan | None = None
    selected_entries: set[int] = field(default_factory=set)
    request_id: int = 0
    busy: bool = False
    cookie_status: dict = field(default_factory=dict)


@dataclass(slots=True)
class ProjectInteractionSnapshot:
    selection: SelectionState
    pending_profile_asset_id: str
    pending_profile_label: str
    pending_profile_placement: TimelinePlacement
    pending_batch_ids: list[str]
    pending_batch_placement: TimelinePlacement
    pending_import_tasks: dict[str, tuple[str, int]]
    pending_import_batches: dict[str, ImportDropBatch]
    pending_relink_asset_id: str
    pending_relink_path: str
    download_plan: DownloadPlan | None
    selected_download_entries: set[int]
    pending_preview_range: tuple[int, int] | None


@dataclass(slots=True)
class RuntimeToolState:
    status: dict
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


@dataclass(slots=True)
class AsyncRequestState:
    recent_id: int = 0
    preview_id: int = 0
    preview_future: Future | None = None
    encoder_id: int = 0
    audio_metrics_id: int = 0
    filmstrip_id: int = 0
    filmstrip_future: Future | None = None
    project_close_id: int = 0
    project_close_future: Future | None = None
    closing_project: DesktopProject | None = None
    closing_project_error: str = ""
    shutting_down: bool = False


@dataclass(slots=True)
class SessionModels:
    assets: AssetListModel
    asset_bins: AssetBinListModel
    asset_moments: AssetMomentListModel
    filtered_asset_moments: AssetMomentFilterModel
    filtered_assets: AssetFilterModel
    sequences: SequenceListModel
    recent_projects: RecentProjectListModel
    download_entries: DownloadEntryListModel
    tracks: TrackListModel
    clips: ClipListModel
    compound_clips: CompoundClipListModel
    transitions: TransitionListModel
    markers: TimelineMarkerListModel
    ranges: TimelineRangeListModel
    web_layers: WebLayerListModel
    tasks: TaskListModel
    documents: SubtitleDocumentListModel
    segments: SubtitleSegmentListModel
    subtitle_placements: SubtitlePlacementListModel
    glossary: GlossaryTermListModel
    llm_providers: LlmProviderListModel
    highlights: HighlightListModel
    audio_buses: AudioBusListModel
    audio_effects: AudioEffectListModel
    audio_effect_parameters: AudioEffectParameterListModel

    @classmethod
    def create(cls, parent: QObject) -> SessionModels:
        assets = AssetListModel(parent)
        asset_moments = AssetMomentListModel(parent)
        return cls(
            assets=assets,
            asset_bins=AssetBinListModel(parent),
            asset_moments=asset_moments,
            filtered_asset_moments=AssetMomentFilterModel(asset_moments, parent),
            filtered_assets=AssetFilterModel(assets, parent),
            sequences=SequenceListModel(parent),
            recent_projects=RecentProjectListModel(parent),
            download_entries=DownloadEntryListModel(parent),
            tracks=TrackListModel(parent),
            clips=ClipListModel(parent),
            compound_clips=CompoundClipListModel(parent),
            transitions=TransitionListModel(parent),
            markers=TimelineMarkerListModel(parent),
            ranges=TimelineRangeListModel(parent),
            web_layers=WebLayerListModel(parent),
            tasks=TaskListModel(parent),
            documents=SubtitleDocumentListModel(parent),
            segments=SubtitleSegmentListModel(parent),
            subtitle_placements=SubtitlePlacementListModel(parent),
            glossary=GlossaryTermListModel(parent),
            llm_providers=LlmProviderListModel(parent),
            highlights=HighlightListModel(parent),
            audio_buses=AudioBusListModel(parent),
            audio_effects=AudioEffectListModel(parent),
            audio_effect_parameters=AudioEffectParameterListModel(parent),
        )
