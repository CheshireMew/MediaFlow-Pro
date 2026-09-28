from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.project_collection_service import ProjectCollectionService
from mediaflow.domain.enums import AssetKind
from mediaflow.infrastructure.project_archive_service import ProjectArchiveService
from mediaflow.infrastructure.project_collection_storage import LocalProjectCollectionStorage
from mediaflow.infrastructure.project_repository import ProjectRepository


def _collection_service(
    repository: ProjectRepository,
) -> tuple[ProjectCollectionService, ProjectEditHistory]:
    history = ProjectEditHistory()
    service = ProjectCollectionService(
        repository,
        LocalProjectCollectionStorage(repository.project_dir),
        history,
    )
    history.register_handler("asset.collection-state", service.apply_history_action)
    return service, history


def test_project_collection_is_verified_and_path_switching_is_reversible(
    tmp_path: Path,
) -> None:
    external = tmp_path / "camera-a.bin"
    external.write_bytes(b"camera source" * 257)
    project_root = tmp_path / "CollectedProject"

    with ProjectRepository.create(project_root, "CollectedProject") as repository:
        asset = repository.assets.import_external_asset(external, AssetKind.VIDEO)
        service, history = _collection_service(repository)

        preview = service.preview()
        assert preview.collectable_count == 1
        assert preview.total_bytes == external.stat().st_size

        result = service.collect()
        collected = repository.assets.get_asset(asset.id)
        collected_path = repository.assets.resolve_asset_path(collected)
        assert result.using_collected_files is True
        assert collected.managed is True
        assert collected_path.read_bytes() == external.read_bytes()
        assert collected_path.is_relative_to(project_root)
        assert service.list_records() == [result.record]

        assert history.undo() == "归集项目素材"
        restored = repository.assets.get_asset(asset.id)
        assert restored.managed is False
        assert Path(restored.path) == external

        assert history.redo() == "归集项目素材"
        recollected = repository.assets.get_asset(asset.id)
        assert recollected.managed is True
        assert repository.assets.resolve_asset_path(recollected) == collected_path


def test_project_collection_rejects_a_modified_managed_copy(tmp_path: Path) -> None:
    external = tmp_path / "voice.wav"
    external.write_bytes(b"voice source")
    with ProjectRepository.create(tmp_path / "IntegrityProject", "IntegrityProject") as repository:
        asset = repository.assets.import_external_asset(external, AssetKind.AUDIO)
        service, _history = _collection_service(repository)
        result = service.collect()
        service.set_state(result.record.id, using_collected=False)
        collected = repository.project_dir / result.record.items[0].collected_path
        collected.write_bytes(b"changed after collection")

        with pytest.raises(RuntimeError, match="归集素材校验失败"):
            service.set_state(result.record.id, using_collected=True)
        assert repository.assets.get_asset(asset.id).managed is False


def test_project_archive_reopens_with_managed_media_and_named_versions(
    tmp_path: Path,
) -> None:
    external = tmp_path / "source.mov"
    external.write_bytes(b"portable project source")
    project_root = tmp_path / "ArchiveSource"
    destination = tmp_path / "ArchiveDelivery"

    with ProjectRepository.create(project_root, "ArchiveSource") as repository:
        asset = repository.assets.import_external_asset(external, AssetKind.VIDEO)
        service, _history = _collection_service(repository)
        service.collect()
        repository.records.create_project_version("Client review")

        result = ProjectArchiveService(repository).create(destination)
        assert result.destination == str(destination)
        manifest = json.loads((destination / "archive-manifest.json").read_text("utf-8"))
        assert manifest["format"] == "mediaflow-project-archive"
        assert manifest["project_id"] == repository.projects.get_project().id
        assert all(not item["path"].startswith("cache/") for item in manifest["files"])

    with ProjectRepository.open(destination, writable=False) as archived:
        archived_asset = archived.assets.get_asset(asset.id)
        assert archived_asset.managed is True
        assert archived.assets.resolve_asset_path(archived_asset).read_bytes() == external.read_bytes()
        assert [version.name for version in archived.records.list_project_versions()] == [
            "Client review"
        ]


def test_project_archive_requires_external_media_to_be_collected(tmp_path: Path) -> None:
    external = tmp_path / "outside.mp4"
    external.write_bytes(b"external")
    with ProjectRepository.create(tmp_path / "UncollectedProject", "Uncollected") as repository:
        repository.assets.import_external_asset(external, AssetKind.VIDEO)
        with pytest.raises(ValueError, match="必须先归集"):
            ProjectArchiveService(repository).create(tmp_path / "ShouldNotPublish")
    assert not (tmp_path / "ShouldNotPublish").exists()
