from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.project_collection_service import ProjectCollectionService
from mediaflow.application.review_package_service import ReviewPackageService
from mediaflow.application.task_service import TaskCompletion, TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.enums import AssetKind, TaskKind, TaskStatus
from mediaflow.domain.task_commands import ExportSequenceCommand
from mediaflow.domain.tasks import Task
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.project_archive_service import ProjectArchiveService
from mediaflow.infrastructure.project_collection_storage import LocalProjectCollectionStorage
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.project_schema_definition import PROJECT_SCHEMA_VERSION
from mediaflow.infrastructure.task_repository import TaskRepository


def test_version_restore_preserves_request_receipts_events_and_task_consumption(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Versions", "Before") as repository:
        version = repository.records.create_project_version("Baseline")
        repository.projects.rename_project("After")
        repository.operations.save_result("request", "edit", "hash", {"result": "done"})
        cursor = repository.events.latest_cursor()
        events = repository.events.list_events()
        project = repository.projects.get_project()
        task = TaskRepository(repository).create(
            Task(
                project_id=project.id,
                sequence_id=project.main_sequence_id,
                command=ExportSequenceCommand(
                    sequence_id=project.main_sequence_id,
                    output_path=str(tmp_path / "x.mp4"),
                ),
                status=TaskStatus.COMPLETED,
            )
        )
        repository.operations.consume_task_result_once(
            task.id,
            project.id,
            task.revision,
            lambda: {"done": True},
        )
        repository.records.restore_project_version(version.id)
        assert repository.projects.get_project().name == "Before"
        assert repository.operations.result("request", "edit", "hash") == {"result": "done"}
        assert repository.operations.committed_task_result(task.id) == {"done": True}
        assert TaskRepository(repository).get(task.id).status == TaskStatus.COMPLETED
        assert repository.events.latest_cursor() > cursor
        assert repository.events.list_events()[: len(events)] == events


def test_archive_materializes_every_named_version_without_original_media(tmp_path: Path) -> None:
    original = tmp_path / "source.srt"
    original.write_text("historical content", encoding="utf-8")
    with ProjectRepository.create(tmp_path / "Source", "Portable") as repository:
        asset = repository.assets.import_external_asset(original, AssetKind.SUBTITLE)
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = TimelineEditor(repository, sequence_id)
        thread = editor.add_review_thread(0, "Review", ActorIdentity(kind="human", id="reviewer"))
        image = tmp_path / "evidence.png"
        from PySide6.QtGui import QImage

        picture = QImage(16, 16, QImage.Format.Format_RGB888)
        picture.fill(0x123456)
        assert picture.save(str(image))
        attachment = ReviewPackageService(
            repository,
            lambda key: TimelineEditor(repository, key),
        ).attach_snapshot(
            sequence_id,
            thread.id,
            frame=0,
            rendered_path=image,
            rendered_sha256=sha256_file(image),
            width=16,
            height=16,
        )
        first = repository.records.create_project_version("Before collection")
        collection = ProjectCollectionService(
            repository,
            LocalProjectCollectionStorage(repository.project_dir),
            ProjectEditHistory(),
        )
        collection.collect()
        second = repository.records.create_project_version("After collection")
        result = ProjectArchiveService(repository).create(tmp_path / "Archive")
    # Move the original away rather than deleting it. A portable copy must not
    # depend on it, nor on the source project's collected media.
    original.rename(tmp_path / "source.saved.srt")
    (tmp_path / "Source").rename(tmp_path / "Source.saved")
    with ProjectRepository.open(Path(result.destination)) as archive:
        for version_id in (first.id, second.id):
            archive.records.restore_project_version(version_id)
            restored = archive.assets.get_asset(asset.id)
            assert restored.managed
            path = archive.assets.resolve_asset_path(restored)
            assert path.is_relative_to(Path(result.destination))
            assert path.read_text(encoding="utf-8") == "historical content"
            assert sha256_file(Path(result.destination) / attachment.relative_path) == attachment.sha256


def test_archive_task_history_never_recovers_or_retries_original_output(tmp_path: Path) -> None:
    destination = tmp_path / "original-final.mp4"
    destination.write_bytes(b"original delivery")
    with ProjectRepository.create(tmp_path / "Source", "Tasks") as repository:
        project = repository.projects.get_project()
        task = TaskRepository(repository).create(
            Task(
                project_id=project.id,
                sequence_id=project.main_sequence_id,
                command=ExportSequenceCommand(
                    sequence_id=project.main_sequence_id,
                    output_path=str(destination),
                    overwrite=True,
                ),
                status=TaskStatus.RUNNING,
                execution_owner_id="old-owner",
                heartbeat_at=1,
                lease_expires_at=2,
            )
        )
        result = ProjectArchiveService(repository).create(tmp_path / "Archive")
        assert TaskRepository(repository).get(task.id).status == TaskStatus.RUNNING
    with ProjectRepository.open(Path(result.destination)) as repository:
        called = []
        service = TaskService(TaskRepository(repository), recovery_poll_interval=0.02)
        service.register(TaskKind.EXPORT, lambda context: called.append(context) or TaskCompletion())
        try:
            assert service.recover_claimable() == 0
            restored = service.get(task.id)
            assert restored.execution_scope == "archive_history"
            assert restored.status == TaskStatus.CANCELLED
            for action in (service.retry, service.resume):
                with pytest.raises(ValueError, match="归档"):
                    action(task.id)
            assert called == []
            assert destination.read_bytes() == b"original delivery"
        finally:
            service.shutdown(timeout=5)


def test_v57_upgrade_adds_execution_scope_to_project_and_version_snapshots(tmp_path: Path) -> None:
    with ProjectRepository.create(tmp_path / "Migration", "Migration") as repository:
        version = repository.records.create_project_version("Version")
        second = repository.records.create_project_version("Second")
        snapshot = repository.project_dir / version.snapshot_path
        second_snapshot = repository.project_dir / second.snapshot_path
        database = repository.database_path
    for path in (snapshot, second_snapshot, database):
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("ALTER TABLE task DROP COLUMN execution_scope")
            connection.execute("UPDATE schema_info SET version=57")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executemany(
            "UPDATE project_version SET sha256=? WHERE id=?",
            (
                (sha256_file(snapshot), version.id),
                (sha256_file(second_snapshot), second.id),
            ),
        )
    with ProjectRepository.open(tmp_path / "Migration") as repository:
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == PROJECT_SCHEMA_VERSION
        assert "execution_scope" in {row["name"] for row in repository._fetchall("PRAGMA table_info(task)")}
        repository.records.restore_project_version(version.id)
        archive = ProjectArchiveService(repository).create(tmp_path / "MigratedArchive")
    with ProjectRepository.open(Path(archive.destination)) as repository:
        for version_id in (version.id, second.id):
            repository.records.restore_project_version(version_id)


def test_archive_restores_real_web_package_with_runtime_and_receipt(tmp_path: Path) -> None:
    from mediaflow.composition import EditorProject
    from mediaflow.domain.enums import TrackKind
    from mediaflow.domain.settings import ServiceSettings
    from mediaflow.infrastructure.runtime_context import RuntimeContext
    from mediaflow.infrastructure.web_render_service import WebRenderService

    paths = RuntimeContext.discover().paths
    repository = ProjectRepository.create(tmp_path / "WebOriginal", "Web")
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    try:
        starter = Path(__file__).resolve().parents[2] / "fixtures" / "editable-media-v6"
        asset = project.import_web_package(starter)
        sequence_id = project.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(track_id=track.id, asset_id=asset.id, timeline_start=0, source_in=0, duration=1)
        version = repository.records.create_project_version("Web version")
        archive = ProjectArchiveService(repository).create(tmp_path / "WebArchive")
    finally:
        project.close(timeout=10)
    (tmp_path / "WebOriginal").rename(tmp_path / "WebOriginal.saved")
    with ProjectRepository.open(Path(archive.destination)) as repository:
        repository.records.restore_project_version(version.id)
        state = repository.timeline.load_timeline(sequence_id)
        outputs = WebRenderService(repository, paths).ensure_sequence(state)
        assert len(outputs) == 1
        assert outputs[0].stat().st_size > 0
