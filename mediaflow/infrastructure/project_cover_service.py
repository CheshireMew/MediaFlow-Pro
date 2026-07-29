from __future__ import annotations

from pathlib import Path

from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import Asset

from .media_thumbnail_service import MediaThumbnailService
from .project_repository import ProjectRepository
from .runtime_paths import RuntimePaths


class ProjectCoverService:
    """Build the recent-project cover from the first readable video asset."""

    WIDTH = 640
    HEIGHT = 360

    def __init__(self, paths: RuntimePaths):
        self._thumbnails = MediaThumbnailService(paths)

    def cover_for(self, repository: ProjectRepository) -> Path | None:
        asset = self._first_video_asset(repository)
        if asset is None:
            return None
        return self._thumbnails.thumbnail_for(
            repository,
            asset,
            width=self.WIDTH,
            height=self.HEIGHT,
        )

    @staticmethod
    def _first_video_asset(repository: ProjectRepository) -> Asset | None:
        for asset in repository.catalog.list_assets():
            if asset.kind != AssetKind.VIDEO and not asset.metadata.has_video:
                continue
            return asset
        return None
