from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QUrl

from mediaflow.application.asset_service import AssetService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.desktop.controllers.controller_hub import EditorControllers
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.subtitle_file_store import LocalSubtitleFileStore
from mediaflow.infrastructure.subtitle_publication_storage import (
    LocalSubtitlePublicationStorage,
)
from tests.v2.real_media import generate_real_media


def test_asset_search_uses_real_linked_transcript_and_multilingual_concepts(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "A001.mp4"
    generate_real_media(source, paths, width=320, height=180)
    subtitle = tmp_path / "A001.zh.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:00,800\n城市夜景与街道灯光\n",
        encoding="utf-8",
    )
    project_dir = tmp_path / "Search Project"
    with ProjectRepository.create(project_dir, "Search Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        media = assets.import_external(source)
        publication = SubtitlePublicationService(repository, LocalSubtitlePublicationStorage())
        SubtitleAcquisitionService(
            repository,
            publication,
            LocalSubtitleFileStore(),
        ).import_subtitle_file(
            subtitle,
            assets,
            media_asset_id=media.id,
        )

    QCoreApplication.instance() or QCoreApplication([])
    controllers = EditorControllers()
    try:
        controllers.workspace_project.openProject(QUrl.fromLocalFile(str(project_dir)).toString())
        source_model = controllers.media.assetsModel
        media_row = source_model.findRow("assetId", media.id)
        assert media_row >= 0
        assert "城市夜景与街道灯光" in source_model.get(media_row)["searchText"]

        controllers.media.setAssetSearchText("night city")
        filtered = controllers.media.filteredAssetsModel
        asset_id_role = next(
            role for role, name in filtered.roleNames().items() if bytes(name).decode() == "assetId"
        )
        matched_ids = {
            filtered.data(filtered.index(row, 0), asset_id_role) for row in range(filtered.rowCount())
        }
        assert media.id in matched_ids

        controllers.media.setAssetSearchText("interview")
        assert filtered.rowCount() == 0
    finally:
        controllers.shutdown()
