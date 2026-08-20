from __future__ import annotations

from dataclasses import dataclass

from mediaflow.infrastructure.asset_catalog_repository import AssetCatalogRepository
from mediaflow.infrastructure.audio_repository import AudioRepository
from mediaflow.infrastructure.dubbing_repository import DubbingRepository
from mediaflow.infrastructure.highlight_repository import HighlightRepository
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
    audio: AudioRepository
    dubbing: DubbingRepository
    subtitles: SubtitleRepository
    highlights: HighlightRepository
    web: WebMediaRepository
    records: ProjectRecordsRepository


def assemble_project_repositories(
    database: ProjectDatabaseSession,
) -> ProjectRepositoryAssembly:
    projects = ProjectMetadataRepository(database)
    sequences = SequenceCatalogRepository(database)
    assets = AssetCatalogRepository(database)
    observations = ProjectObservationRepository(database)
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
        timeline=TimelineRepository(database),
        audio=AudioRepository(database),
        dubbing=DubbingRepository(database),
        subtitles=SubtitleRepository(database),
        highlights=HighlightRepository(database),
        web=WebMediaRepository(database),
        records=ProjectRecordsRepository(database),
    )
    database.relations.bind(
        projects=assembly.projects,
        sequences=assembly.sequences,
        assets=assembly.assets,
        observations=assembly.observations,
        timeline=assembly.timeline,
        audio=assembly.audio,
        dubbing=assembly.dubbing,
        subtitles=assembly.subtitles,
        highlights=assembly.highlights,
        web=assembly.web,
        records=assembly.records,
    )
    return assembly
