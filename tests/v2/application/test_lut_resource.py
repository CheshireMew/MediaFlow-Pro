from __future__ import annotations

from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import AssetKind, TrackKind, VisualEffectKind
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext


def _identity_cube() -> str:
    return """TITLE "Test identity"
LUT_3D_SIZE 2
DOMAIN_MIN 0 0 0
DOMAIN_MAX 1 1 1
0 0 0
1 0 0
0 1 0
1 1 0
0 0 1
1 0 1
0 1 1
1 1 1
"""


def test_lut_is_validated_content_addressed_and_compiled_as_a_clip_effect(
    tmp_path: Path,
) -> None:
    source = tmp_path / "identity.cube"
    source.write_text(_identity_cube(), encoding="utf-8")
    video = tmp_path / "source.mp4"
    video.write_bytes(b"timeline-source")
    with ProjectRepository.create(tmp_path / "LUT Project", "LUT Project") as repository:
        lut = AssetService(
            repository,
            probe=None,
            fingerprint_file=fingerprint_file,
        ).import_lut(source)
        reused = AssetService(
            repository,
            probe=None,
            fingerprint_file=fingerprint_file,
        ).import_lut(source)
        assert lut.id == reused.id
        assert lut.kind == AssetKind.LUT
        assert lut.managed is True
        resolved_lut = repository.assets.resolve_asset_path(lut)
        assert resolved_lut.is_relative_to(repository.project_dir)
        assert resolved_lut.read_text(encoding="utf-8") == _identity_cube()

        video_asset = repository.assets.import_external_asset(video, AssetKind.VIDEO)
        project = repository.projects.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=video_asset.id,
            timeline_start=0,
            source_in=0,
            duration=10,
        )
        effect = editor.add_clip_visual_effect(
            clip.id,
            VisualEffectKind.LUT_3D,
            resource_asset_id=lut.id,
        )
        document = TimelineCompiler(
            repository,
            RuntimeContext.discover().paths,
        ).compile(editor.state)

        assert effect.resource_asset_id == lut.id
        assert "avfilter.lut3d" in document.xml
        assert "av.file" in document.xml
        assert str(resolved_lut) in document.xml
        assert resolved_lut in document.source_paths


def test_lut_import_rejects_incomplete_cube_data(tmp_path: Path) -> None:
    source = tmp_path / "broken.cube"
    source.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")
    with ProjectRepository.create(tmp_path / "Broken LUT", "Broken LUT") as repository:
        with pytest.raises(ValueError, match="8"):
            AssetService(
                repository,
                probe=None,
                fingerprint_file=fingerprint_file,
            ).import_lut(source)
