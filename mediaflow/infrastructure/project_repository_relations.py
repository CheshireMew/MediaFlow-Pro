from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaflow.infrastructure.asset_catalog_repository import AssetCatalogRepository
    from mediaflow.infrastructure.audio_repository import AudioRepository
    from mediaflow.infrastructure.dubbing_repository import DubbingRepository
    from mediaflow.infrastructure.highlight_repository import HighlightRepository
    from mediaflow.infrastructure.project_metadata_repository import ProjectMetadataRepository
    from mediaflow.infrastructure.project_observation_repository import (
        ProjectObservationRepository,
    )
    from mediaflow.infrastructure.project_records_repository import (
        ProjectRecordsRepository,
    )
    from mediaflow.infrastructure.sequence_catalog_repository import SequenceCatalogRepository
    from mediaflow.infrastructure.subtitle_repository import SubtitleRepository
    from mediaflow.infrastructure.timeline_repository import TimelineRepository
    from mediaflow.infrastructure.web_media_repository import WebMediaRepository


class ProjectRepositoryRelations:
    """Explicit collaborators for repositories that span aggregate tables."""

    def __init__(self) -> None:
        self._bound = False

    def bind(
        self,
        *,
        projects: ProjectMetadataRepository,
        sequences: SequenceCatalogRepository,
        assets: AssetCatalogRepository,
        observations: ProjectObservationRepository,
        timeline: TimelineRepository,
        audio: AudioRepository,
        dubbing: DubbingRepository,
        subtitles: SubtitleRepository,
        highlights: HighlightRepository,
        web: WebMediaRepository,
        records: ProjectRecordsRepository,
    ) -> None:
        if self._bound:
            raise RuntimeError("Project repository relations are already bound")
        self._projects = projects
        self._sequences = sequences
        self._assets = assets
        self._observations = observations
        self._timeline = timeline
        self._audio = audio
        self._dubbing = dubbing
        self._subtitles = subtitles
        self._highlights = highlights
        self._web = web
        self._records = records
        self._bound = True

    def _required(self, name: str) -> Any:
        if not self._bound:
            raise RuntimeError("Project repository relations are not bound")
        return getattr(self, name)

    @property
    def projects(self) -> ProjectMetadataRepository:
        return self._required("_projects")

    @property
    def sequences(self) -> SequenceCatalogRepository:
        return self._required("_sequences")

    @property
    def assets(self) -> AssetCatalogRepository:
        return self._required("_assets")

    @property
    def observations(self) -> ProjectObservationRepository:
        return self._required("_observations")

    @property
    def timeline(self) -> TimelineRepository:
        return self._required("_timeline")

    @property
    def audio(self) -> AudioRepository:
        return self._required("_audio")

    @property
    def dubbing(self) -> DubbingRepository:
        return self._required("_dubbing")

    @property
    def subtitles(self) -> SubtitleRepository:
        return self._required("_subtitles")

    @property
    def highlights(self) -> HighlightRepository:
        return self._required("_highlights")

    @property
    def web(self) -> WebMediaRepository:
        return self._required("_web")

    @property
    def records(self) -> ProjectRecordsRepository:
        return self._required("_records")
