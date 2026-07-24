from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import TrackKind
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import LoudnessAnalysisService, TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths


def test_real_sequence_audio_graph_reports_peak_and_ebu_r128_metrics(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "constant-tone.mp4"
    generated = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=25:d=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=5",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr

    with ProjectRepository.create(tmp_path / "Loudness Project", "Loudness Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        asset = assets.import_external(source)
        asset = assets.adopt_main_profile_from_video(asset.id)
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=125,
        )

        progress = []
        metrics, result_path = LoudnessAnalysisService(
            TimelineCompiler(repository),
            paths,
        ).analyze(editor.state, progress=progress.append)

        assert -30.0 < metrics.sample_peak_dbfs < -10.0
        assert metrics.true_peak_dbtp == pytest.approx(metrics.sample_peak_dbfs, abs=1.0)
        assert -35.0 < metrics.integrated_lufs < -10.0
        assert metrics.short_term_lufs == pytest.approx(metrics.integrated_lufs, abs=1.0)
        assert result_path.is_file()
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload["sequence_id"] == editor.state.sequence.id
        assert payload["integrated_lufs"] == metrics.integrated_lufs
        assert (repository.project_dir / payload["rendered_audio"]).is_file()
        render_progress = [
            item for item in progress if item.message_code == "audio_analysis_rendering"
        ]
        loudness_progress = [
            item
            for item in progress
            if item.message_code == "audio_analysis_measuring_loudness"
        ]
        assert render_progress[-1].completed == render_progress[-1].total
        assert loudness_progress[-1].completed == loudness_progress[-1].total
