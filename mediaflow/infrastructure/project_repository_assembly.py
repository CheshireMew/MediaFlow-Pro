from __future__ import annotations

from dataclasses import dataclass

from mediaflow.infrastructure.asset_catalog_repository import AssetCatalogRepository
from mediaflow.infrastructure.audio_repository import AudioRepository
from mediaflow.infrastructure.dubbing_repository import DubbingRepository
from mediaflow.infrastructure.highlight_repository import HighlightRepository
from mediaflow.infrastructure.main_frame_clock_repository import MainFrameClockRepository
from mediaflow.infrastructure.project_database_session import ProjectDatabaseSession
from mediaflow.infrastructure.project_event_repository import ProjectEventRepository
from mediaflow.infrastructure.project_history_repository import ProjectHistoryRepository
from mediaflow.infrastructure.project_metadata_repository import ProjectMetadataRepository
from mediaflow.infrastructure.project_observation_repository import (
    ProjectObservationRepository,
)
from mediaflow.infrastructure.project_operation_repository import (
    ProjectOperationRepository,
)
from mediaflow.infrastructure.project_records_repository import ProjectRecordsRepository
from mediaflow.infrastructure.project_repository_relations import ProjectObservationSources
from mediaflow.infrastructure.sequence_catalog_repository import SequenceCatalogRepository
from mediaflow.infrastructure.subtitle_repository import SubtitleRepository
from mediaflow.infrastructure.timeline_repository import TimelineRepository
from mediaflow.infrastructure.web_media_repository import WebMediaRepository


@dataclass(frozen=True, slots=True)
class ProjectRepositoryAssembly:
    projects: ProjectMetadataRepository
    sequences: SequenceCatalogRepository
    assets: AssetCatalogRepository
    observations: ProjectObservationRepository
    events: ProjectEventRepository
    history: ProjectHistoryRepository
    operations: ProjectOperationRepository
    timeline: TimelineRepository
    frame_clock: MainFrameClockRepository
    audio: AudioRepository
    dubbing: DubbingRepository
    subtitles: SubtitleRepository
    highlights: HighlightRepository
    web: WebMediaRepository
    records: ProjectRecordsRepository


def assemble_project_repositories(
    database: ProjectDatabaseSession,
) -> ProjectRepositoryAssembly:
    projects = ProjectMetadataRepository(
        database,
        sequences=lambda: sequences,
        assets=lambda: assets,
    )
    sequences = SequenceCatalogRepository(
        database,
        projects=lambda: projects,
        audio=lambda: audio,
    )
    assets = AssetCatalogRepository(database, projects=lambda: projects)
    audio = AudioRepository(database, sequences=lambda: sequences)
    highlights = HighlightRepository(database, projects=lambda: projects)
    subtitles = SubtitleRepository(
        database,
        projects=lambda: projects,
        assets=lambda: assets,
        sequences=lambda: sequences,
    )
    web = WebMediaRepository(database, assets=lambda: assets)
    timeline = TimelineRepository(
        database,
        projects=lambda: projects,
        sequences=lambda: sequences,
        assets=lambda: assets,
        subtitles=lambda: subtitles,
        web=lambda: web,
    )
    frame_clock = MainFrameClockRepository(
        database,
        projects=lambda: projects,
        sequences=lambda: sequences,
        assets=lambda: assets,
        subtitles=lambda: subtitles,
        highlights=lambda: highlights,
        timeline=lambda: timeline,
    )
    dubbing = DubbingRepository(
        database,
        projects=lambda: projects,
        sequences=lambda: sequences,
        subtitles=lambda: subtitles,
        timeline=lambda: timeline,
    )
    records = ProjectRecordsRepository(
        database,
        projects=lambda: projects,
        sequences=lambda: sequences,
    )
    observations = ProjectObservationRepository(
        database,
        sources=ProjectObservationSources(
            projects=projects,
            sequences=sequences,
            assets=assets,
            timeline=timeline,
            audio=audio,
            dubbing=dubbing,
            subtitles=subtitles,
            highlights=highlights,
            web=web,
            records=records,
        ),
    )
    events = ProjectEventRepository(
        database,
        observe_changes=observations.capture,
        enlist_publication=database.enlist_transaction_publication,
    )
    assembly = ProjectRepositoryAssembly(
        projects=projects,
        sequences=sequences,
        assets=assets,
        observations=observations,
        events=events,
        history=ProjectHistoryRepository(database),
        operations=ProjectOperationRepository(database),
        timeline=timeline,
        frame_clock=frame_clock,
        audio=audio,
        dubbing=dubbing,
        subtitles=subtitles,
        highlights=highlights,
        web=web,
        records=records,
    )
    return assembly
