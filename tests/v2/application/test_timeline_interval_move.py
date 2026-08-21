from pathlib import Path

import pytest

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.timeline_interval_move import TimelineIntervalMovePolicy
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.timeline import TimelineMarker
from mediaflow.infrastructure.project_repository import ProjectRepository


def _timeline_with_one_clip(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"interval-move-source")
    repository = ProjectRepository.create(tmp_path / "Project", "Project")
    asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
    sequence_id = repository.projects.get_project().main_sequence_id
    editor = TimelineEditor(repository, sequence_id)
    track = editor.add_track(TrackKind.VIDEO)
    editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=30,
    )
    return repository, editor, track.id


def test_move_interval_forward_preserves_frames_and_total_duration(tmp_path: Path) -> None:
    repository, editor, _track_id = _timeline_with_one_clip(tmp_path)
    try:
        state = editor.state
        state.markers = [
            TimelineMarker(sequence_id=editor.sequence_id, frame=7),
            TimelineMarker(sequence_id=editor.sequence_id, frame=12),
            TimelineMarker(sequence_id=editor.sequence_id, frame=25),
        ]

        TimelineIntervalMovePolicy(5, 10, 20).apply(state)

        assert state.duration_frames == 30
        assert [
            (clip.timeline_start, clip.source_in, clip.duration) for clip in state.clips
        ] == [
            (0, 0, 5),
            (5, 10, 10),
            (15, 5, 5),
            (20, 20, 10),
        ]
        assert [marker.frame for marker in state.markers] == [17, 7, 25]
    finally:
        repository.close()


def test_move_interval_rejects_an_intersecting_locked_track(tmp_path: Path) -> None:
    repository, editor, track_id = _timeline_with_one_clip(tmp_path)
    try:
        state = editor.state
        state.tracks = [
            track.model_copy(update={"locked": track.id == track_id})
            for track in state.tracks
        ]

        with pytest.raises(ValueError, match="锁定轨道"):
            TimelineIntervalMovePolicy(5, 10, 20).apply(state)
    finally:
        repository.close()
