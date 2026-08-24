from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mediaflow.infrastructure.asset_catalog_repository import AssetCatalogRepository
    from mediaflow.infrastructure.audio_repository import AudioRepository
    from mediaflow.infrastructure.dubbing_repository import DubbingRepository
    from mediaflow.infrastructure.highlight_repository import HighlightRepository
    from mediaflow.infrastructure.project_metadata_repository import ProjectMetadataRepository
    from mediaflow.infrastructure.project_records_repository import (
        ProjectRecordsRepository,
    )
    from mediaflow.infrastructure.sequence_catalog_repository import SequenceCatalogRepository
    from mediaflow.infrastructure.subtitle_repository import SubtitleRepository
    from mediaflow.infrastructure.timeline_repository import TimelineRepository
    from mediaflow.infrastructure.web_media_repository import WebMediaRepository


@dataclass(frozen=True, slots=True)
class ProjectObservationSources:
    """Read-only projection sources used only to observe the complete project."""

    projects: ProjectMetadataRepository
    sequences: SequenceCatalogRepository
    assets: AssetCatalogRepository
    timeline: TimelineRepository
    audio: AudioRepository
    dubbing: DubbingRepository
    subtitles: SubtitleRepository
    highlights: HighlightRepository
    web: WebMediaRepository
    records: ProjectRecordsRepository
