from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.storage_names import utf16_units
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import LoudnessAnalysisService, TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths


def test_real_sequence_audio_graph_reports_peak_and_ebu_r128_metrics(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
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

    project_dir = max_project_path
    with ProjectRepository.create(project_dir, "Loudness Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        asset = assets.import_external(source)
        asset = assets.adopt_main_profile_from_video(asset.id)
        editor = TimelineEditor(repository, repository.catalog.get_project().main_sequence_id)
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
        assert len(payload["snapshot_hash"]) == 64
        assert result_path == LoudnessAnalysisService.result_path(
            project_dir,
            editor.sequence_id,
            payload["snapshot_hash"],
        )
        source_graph = repository.project_dir / payload["source_graph"]
        assert source_graph.is_file()
        assert utf16_units(str(source_graph)) <= 240
        assert utf16_units(str(result_path)) <= 240
        assert not list((repository.project_dir / "cache" / "l").glob("*.wav"))
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

        service = LoudnessAnalysisService(TimelineCompiler(repository), paths)
        first_hash = service.snapshot_hash(editor.state)
        assert first_hash == payload["snapshot_hash"]
        master = next(
            bus
            for bus in repository.audio.list_audio_buses(editor.sequence_id)
            if bus.parent_bus_id is None
        )
        repository.audio.save_audio_bus(master.model_copy(update={"gain_db": -4.0}))
        current_state = repository.timeline.load_timeline(editor.sequence_id)
        current_hash = service.snapshot_hash(current_state)
        assert current_hash != first_hash
        assert (
            service.read_metrics(
                service.result_path(project_dir, editor.sequence_id, current_hash),
                expected_sequence_id=editor.sequence_id,
                expected_snapshot_hash=current_hash,
            )
            is None
        )

        _, current_result = service.analyze(current_state)
        changed_state = current_state.model_copy(deep=True)
        changed_clip = changed_state.clips[0]
        changed_state.clips[0] = changed_clip.model_copy(
            update={
                "audio": changed_clip.audio.model_copy(
                    update={"gain_db": changed_clip.audio.gain_db - 6.0}
                )
            }
        )
        changed_hash = service.snapshot_hash(changed_state)
        assert changed_hash != current_hash
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(
                    LoudnessAnalysisService(TimelineCompiler(repository), paths).analyze,
                    current_state,
                ),
                executor.submit(
                    LoudnessAnalysisService(TimelineCompiler(repository), paths).analyze,
                    current_state,
                ),
                executor.submit(
                    LoudnessAnalysisService(TimelineCompiler(repository), paths).analyze,
                    changed_state,
                ),
            ]
            concurrent_results = [future.result(timeout=60) for future in futures]
        assert concurrent_results[0][1] == concurrent_results[1][1] == current_result
        assert concurrent_results[2][1] != current_result
        assert concurrent_results[2][1] == service.result_path(
            project_dir,
            editor.sequence_id,
            changed_hash,
        )
        assert not list((project_dir / "cache" / "l").glob("*.wav"))
        assert not list((project_dir / "cache" / "l").glob(".mf-*"))

        regenerated = subprocess.run(
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
                "sine=frequency=440:sample_rate=48000:duration=5",
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
        assert regenerated.returncode == 0, regenerated.stderr
        with pytest.raises(RuntimeError, match="刷新素材"):
            service.snapshot_hash(current_state)
        assets.refresh_all()
        refreshed_state = repository.timeline.load_timeline(editor.sequence_id)
        refreshed_hash = service.snapshot_hash(refreshed_state)
        assert refreshed_hash != current_hash
        assert (
            service.read_metrics(
                service.result_path(project_dir, editor.sequence_id, refreshed_hash),
                expected_sequence_id=editor.sequence_id,
                expected_snapshot_hash=refreshed_hash,
            )
            is None
        )
        _, refreshed_result = service.analyze(refreshed_state)
        assert refreshed_result == service.result_path(
            project_dir,
            editor.sequence_id,
            refreshed_hash,
        )
        current_hash = refreshed_hash

    with ProjectRepository.open(project_dir) as reopened:
        state = reopened.timeline.load_timeline(editor.sequence_id)
        reopened_service = LoudnessAnalysisService(TimelineCompiler(reopened), paths)
        reopened_hash = reopened_service.snapshot_hash(state)
        assert reopened_hash == current_hash
        reopened_metrics = reopened_service.read_metrics(
            reopened_service.result_path(
                project_dir,
                editor.sequence_id,
                reopened_hash,
            ),
            expected_sequence_id=editor.sequence_id,
            expected_snapshot_hash=reopened_hash,
        )
        assert reopened_metrics is not None
