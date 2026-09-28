from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtGui import QColor, QImage

from mediaflow.application.review_package_service import ReviewPackageService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.infrastructure.project_repository import ProjectRepository


def test_review_snapshot_markup_and_verified_package_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "review-source.mp4"
    source.write_bytes(b"review-source")
    with ProjectRepository.create(tmp_path / "Review Package", "Review Package") as repository:
        project = repository.projects.get_project()
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={"duration_frames": 30, "has_video": True}
                    )
                }
            )
        )
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )
        thread = editor.add_review_thread(
            8,
            "请检查这个画面",
            ActorIdentity(kind="human", id="reviewer", name="Reviewer"),
        )
        image_path = tmp_path / "proof.png"
        image = QImage(320, 180, QImage.Format.Format_RGB888)
        image.fill(QColor("#223344"))
        assert image.save(str(image_path), "PNG")
        digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
        service = ReviewPackageService(
            repository,
            lambda sequence_id: TimelineEditor(repository, sequence_id),
        )

        snapshot = service.attach_snapshot(
            project.main_sequence_id,
            thread.id,
            frame=8,
            rendered_path=image_path,
            rendered_sha256=digest,
            width=320,
            height=180,
        )
        updated = service.set_markup(
            project.main_sequence_id,
            thread.id,
            snapshot.id,
            [
                {
                    "kind": "arrow",
                    "points": [{"x": 0.2, "y": 0.3}, {"x": 0.7, "y": 0.6}],
                    "color": "#ffcc55",
                    "width": 4,
                }
            ],
        )
        assert updated.snapshots[0].markup[0].kind == "arrow"
        assert (repository.project_dir / updated.snapshots[0].relative_path).is_file()

        package_path = tmp_path / "review.mfr"
        exported = service.export_package(project.main_sequence_id, package_path)
        assert exported.thread_count == 1
        assert exported.snapshot_count == 1
        imported = service.import_package(project.main_sequence_id, package_path)
        assert imported.thread_count == 1
        assert imported.snapshot_count == 1
        assert imported.imported_thread_ids[0] != thread.id
        state = repository.timeline.load_timeline(project.main_sequence_id)
        assert len(state.review_threads) == 2
        assert state.review_threads[1].snapshots[0].markup[0].kind == "arrow"
