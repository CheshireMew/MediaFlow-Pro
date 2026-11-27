from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import ExportFormat, TaskStatus, TrackKind
from mediaflow.domain.exports import ExportPreset, SubtitleStyle
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import BuildSequenceCommand, ExportSequenceCommand, SequenceBuildUnit
from mediaflow.environment import test_run_root as configured_test_root
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt.compiler import TimelineCompiler
from mediaflow.infrastructure.mlt.export_service import MltExportService
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.segmented_export_service import SegmentedExportService
from mediaflow.infrastructure.task_runtime import InfrastructureExportTaskRuntime


def _video(path, paths, color, frames=50):
    result = FfmpegRunner(paths.ffmpeg).run(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r=25:d={frames / 25}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(path),
        ],
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def _preset(**updates):
    return ExportPreset(
        name="Contract",
        format=ExportFormat.H264,
        container="mp4",
        encoder_policy={"mode": "software"},
        audio_codec=updates.pop("audio_codec", None),
        pixel_format="yuv420p",
        preset="ultrafast",
        quality_value=18,
        **updates,
    )


def _timeline(repository, paths, source):
    assets = AssetService(repository, MediaProbe(paths))
    asset = assets.import_external(source)
    editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
    track = editor.add_track(TrackKind.VIDEO, "Video")
    editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=asset.metadata.duration_frames,
    )
    return editor, asset


def _subtitles(repository, editor, asset):
    track = editor.add_track(TrackKind.SUBTITLE, "English")
    document = SubtitleDocument(project_id=asset.project_id, asset_id=asset.id, language="en")
    repository.subtitles.create_subtitle_document(
        document,
        [
            SubtitleSegment(
                document_id=document.id,
                start_frame=5,
                end_frame=20,
                text="SUBTITLE",
            )
        ],
    )
    repository.subtitles.place_subtitle_document(document.id, track.id)
    return track


def _frame(path, paths, frame=10):
    result = FfmpegRunner(paths.ffmpeg).run(
        [
            "-i",
            str(path),
            "-vf",
            f"select=eq(n\\,{frame})",
            "-frames:v",
            "1",
            "-f",
            "framemd5",
            "-",
        ],
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()[-1].split(",")[-1].strip()


def test_render_snapshot_keeps_subtitles_and_buses_consistent_while_live_edits_commit(tmp_path: Path):
    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    source = tmp_path / "source.mp4"
    _video(source, paths, "black")
    with ProjectRepository.create(
        tmp_path / "Project",
        "Snapshot",
        ProjectProfile(width=160, height=90, fps_numerator=25),
    ) as repository:
        editor, asset = _timeline(repository, paths, source)
        track = _subtitles(repository, editor, asset)
        document = repository.subtitles.list_subtitle_documents()[0]
        segment = repository.subtitles.list_subtitle_segments(document.id)[0]
        runtime = InfrastructureExportTaskRuntime(repository, paths)
        with runtime.snapshot() as (documents, frozen_runtime):
            state = documents.timeline.load_timeline(editor.sequence_id)
            revision = documents.content_revision()
            # No source SQLite read lock survives snapshot capture. This edit
            # must commit while export continues reading its detached snapshot.
            repository.subtitles.save_subtitle_segments(
                document.id,
                [segment.model_copy(update={"text": "LATER MANUAL EDIT"})],
            )
            assert repository.content_revision() > revision
            assert documents.content_revision() == revision
            result = frozen_runtime.export_sequence(
                state,
                _preset(burn_subtitle_track_id=track.id),
                tmp_path / "snapshot.mp4",
                overwrite=False,
                progress=lambda _: None,
                check_cancelled=lambda: None,
            )
            assert "SUBTITLE" in result.subtitle_files[0].read_text(encoding="utf-8-sig")
            assert "LATER MANUAL EDIT" not in result.project_graph_path.read_text(encoding="utf-8")
            cold = MltExportService(TimelineCompiler(documents, paths), paths).export(
                state,
                _preset(burn_subtitle_track_id=track.id),
                tmp_path / "cold.mp4",
            )
            assert _frame(result.output_path, paths) == _frame(cold.output_path, paths)


def test_watermark_file_change_invalidates_warm_units_and_matches_cold_render(tmp_path: Path):
    from PySide6.QtGui import QColor, QImage

    from mediaflow.domain.enums import AssetKind
    from mediaflow.domain.exports import WatermarkOverlay

    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    source = tmp_path / "source.mp4"
    _video(source, paths, "black")
    watermark = tmp_path / "mark.png"

    def write_image(color):
        image = QImage(40, 40, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(watermark))

    write_image("red")
    with ProjectRepository.create(
        tmp_path / "Project",
        "Watermark",
        ProjectProfile(width=160, height=90, fps_numerator=25),
    ) as repository:
        editor, _asset = _timeline(repository, paths, source)
        mark = repository.assets.import_external_asset(watermark, AssetKind.IMAGE)
        preset = _preset(watermark=WatermarkOverlay(enabled=True, asset_id=mark.id, width_ratio=0.5))
        service = SegmentedExportService(repository, paths)
        units = [SequenceBuildUnit(id="a", start_frame=0, end_frame=50)]
        first = service.build(editor.state, preset, units, tmp_path / "red.mp4", overwrite=False)
        write_image("blue")
        second = service.build(editor.state, preset, units, tmp_path / "blue.mp4", overwrite=False)
        assert second.units[0].status == "rendered"
        assert second.units[0].cache_key != first.units[0].cache_key
        assert _frame(first.export.output_path, paths) != _frame(second.export.output_path, paths)
        cold = service.exporter.export(editor.state, preset, tmp_path / "cold-blue.mp4")
        assert _frame(second.export.output_path, paths) == _frame(cold.output_path, paths)


def test_visual_keys_follow_solo_and_inherited_style(tmp_path: Path):
    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    source = tmp_path / "source.mp4"
    _video(source, paths, "black")
    with ProjectRepository.create(
        tmp_path / "Project",
        "Keys",
        ProjectProfile(
            width=160,
            height=90,
            fps_numerator=25,
        ),
    ) as repository:
        editor, asset = _timeline(repository, paths, source)
        second = editor.add_track(TrackKind.VIDEO, "Overlay")
        editor.add_clip(track_id=second.id, asset_id=asset.id, timeline_start=0, source_in=0, duration=50)
        subtitle = _subtitles(repository, editor, asset)
        service = SegmentedExportService(repository, paths)
        unit = SequenceBuildUnit(id="whole", start_frame=0, end_frame=50)
        preset = _preset(burn_subtitle_track_id=subtitle.id)
        baseline = service._prepare_visual_unit(editor.state, preset, unit).cache_key
        editor.set_track_state(second.id, enabled=True, locked=False, muted=False, solo=True)
        assert service._prepare_visual_unit(editor.state, preset, unit).cache_key != baseline
        editor.set_track_state(second.id, enabled=True, locked=False, muted=False, solo=False)
        editor.set_subtitle_track_style(subtitle.id, SubtitleStyle(font_size=60))
        assert service._prepare_visual_unit(editor.state, preset, unit).cache_key != baseline


def test_segmented_export_publishes_full_sidecars_and_rolls_back_the_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    source = tmp_path / "source.mp4"
    _video(source, paths, "red")
    with ProjectRepository.create(
        tmp_path / "Project",
        "Sidecars",
        ProjectProfile(
            width=160,
            height=90,
            fps_numerator=25,
        ),
    ) as repository:
        editor, asset = _timeline(repository, paths, source)
        _subtitles(repository, editor, asset)
        service = SegmentedExportService(repository, paths)
        units = [
            SequenceBuildUnit(id="a", start_frame=0, end_frame=25),
            SequenceBuildUnit(id="b", start_frame=25, end_frame=50),
        ]
        output = repository.project_dir / "exports" / "whole.mp4"
        first = service.build(editor.state, _preset(), units, output, overwrite=False)
        assert len(first.export.subtitle_files) == 1
        sidecar = first.export.subtitle_files[0]
        assert "SUBTITLE" in sidecar.read_text(encoding="utf-8-sig")
        conflict = output.with_name("conflict.mp4")
        existing_sidecar = sidecar.with_name(sidecar.name.replace("whole", "conflict", 1))
        existing_sidecar.write_text("existing subtitle", encoding="utf-8")
        with pytest.raises(FileExistsError):
            service.build(editor.state, _preset(), units, conflict, overwrite=False)
        assert not conflict.exists()
        assert existing_sidecar.read_text(encoding="utf-8") == "existing subtitle"
        old = {path: path.read_bytes() for path in (output, sidecar)}
        # Use the ordinary task runtime's automatic strategy at a small test threshold.
        monkeypatch.setattr("mediaflow.infrastructure.segmented_export_service.AUTO_SEGMENT_SECONDS", 1)
        automatic = InfrastructureExportTaskRuntime(repository, paths).export_sequence(
            editor.state,
            _preset(),
            repository.project_dir / "exports" / "automatic.mp4",
            overwrite=False,
            progress=lambda _value: None,
            check_cancelled=lambda: None,
        )
        assert len(automatic.subtitle_files) == 1
        assert automatic.subtitle_files[0].read_bytes() == sidecar.read_bytes()
        original_replace = Path.replace
        injected = False

        def fail_sidecar(source_path, destination):
            nonlocal injected
            if (
                not injected
                and Path(destination).resolve() == sidecar
                and source_path.name.startswith(".mf-subtitle-")
            ):
                injected = True
                raise OSError("injected sidecar failure")
            return original_replace(source_path, destination)

        monkeypatch.setattr(Path, "replace", fail_sidecar)
        with pytest.raises(OSError, match="injected sidecar failure"):
            service.build(editor.state, _preset(), units, output, overwrite=True)
        assert injected
        assert all(path.read_bytes() == data for path, data in old.items())


def test_silent_segmented_export_keeps_the_requested_audio_stream(tmp_path: Path):
    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    source = tmp_path / "silent.mp4"
    _video(source, paths, "red")
    with ProjectRepository.create(
        tmp_path / "Project",
        "Silent",
        ProjectProfile(
            width=160,
            height=90,
            fps_numerator=25,
        ),
    ) as repository:
        editor, _asset = _timeline(repository, paths, source)
        service = SegmentedExportService(repository, paths)
        units = [
            SequenceBuildUnit(id="a", start_frame=0, end_frame=25),
            SequenceBuildUnit(id="b", start_frame=25, end_frame=50),
        ]
        result = service.build(
            editor.state,
            _preset(audio_codec="aac"),
            units,
            repository.project_dir / "exports" / "silent.mp4",
            overwrite=False,
        )
        assert any(stream["codec_type"] == "audio" for stream in result.export.probe["streams"])
        assert result.audio.output_path is not None
        probe = service.exporter.probe(result.audio.output_path)
        assert abs(float(probe["format"]["duration"]) - 2) < 0.08
        ordinary = service.exporter.export(
            editor.state, _preset(audio_codec="aac"), repository.project_dir / "exports" / "ordinary.mp4"
        )
        assert any(stream["codec_type"] == "audio" for stream in ordinary.probe["streams"])


def test_warm_visual_result_changes_with_inherited_style_and_matches_cold(tmp_path: Path):
    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    source = tmp_path / "black.mp4"
    _video(source, paths, "black")
    with ProjectRepository.create(
        tmp_path / "Project",
        "Style",
        ProjectProfile(
            width=160,
            height=90,
            fps_numerator=25,
        ),
    ) as repository:
        editor, asset = _timeline(repository, paths, source)
        subtitle = _subtitles(repository, editor, asset)
        preset = _preset(burn_subtitle_track_id=subtitle.id)
        service = SegmentedExportService(repository, paths)
        units = [SequenceBuildUnit(id="a", start_frame=0, end_frame=50)]
        first = service.build(
            editor.state, preset, units, repository.project_dir / "exports" / "first.mp4", overwrite=False
        )
        editor.set_subtitle_track_style(subtitle.id, SubtitleStyle(font_size=60))
        second = service.build(
            editor.state, preset, units, repository.project_dir / "exports" / "second.mp4", overwrite=False
        )
        assert second.units[0].status == "rendered"
        assert _frame(first.export.output_path, paths) != _frame(second.export.output_path, paths)
        cold = MltExportService(TimelineCompiler(repository, paths), paths).export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "cold.mp4",
        )
        assert _frame(cold.output_path, paths) == _frame(second.export.output_path, paths)


def test_solo_updates_affected_picture_and_reuses_the_unaffected_unit(tmp_path: Path):
    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    black, red = tmp_path / "black.mp4", tmp_path / "red.mp4"
    _video(black, paths, "black")
    _video(red, paths, "red", frames=25)
    with ProjectRepository.create(
        tmp_path / "Project", "Solo", ProjectProfile(width=160, height=90, fps_numerator=25)
    ) as repository:
        editor, _ = _timeline(repository, paths, black)
        base = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        red_asset = AssetService(repository, MediaProbe(paths)).import_external(red)
        overlay = editor.add_track(TrackKind.VIDEO, "Overlay")
        editor.add_clip(
            track_id=overlay.id, asset_id=red_asset.id, timeline_start=0, source_in=0, duration=25
        )
        units = [
            SequenceBuildUnit(id="a", start_frame=0, end_frame=25),
            SequenceBuildUnit(id="b", start_frame=25, end_frame=50),
        ]
        service = SegmentedExportService(repository, paths)
        first = service.build(
            editor.state, _preset(), units, repository.project_dir / "exports" / "first.mp4", overwrite=False
        )
        editor.set_track_state(base.id, enabled=True, locked=False, muted=False, solo=True)
        second = service.build(
            editor.state, _preset(), units, repository.project_dir / "exports" / "solo.mp4", overwrite=False
        )
        assert [unit.status for unit in second.units] == ["rendered", "reused"]
        assert _frame(first.export.output_path, paths) != _frame(second.export.output_path, paths)
        cold = service.exporter.export(
            editor.state, _preset(), repository.project_dir / "exports" / "cold.mp4"
        )
        assert _frame(cold.output_path, paths) == _frame(second.export.output_path, paths)


def test_short_audio_master_includes_silent_tail_and_is_reused(tmp_path: Path):
    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    video, audio = tmp_path / "video.mp4", tmp_path / "short.wav"
    _video(video, paths, "red")
    generated = FfmpegRunner(paths.ffmpeg).run(
        ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1", "-y", str(audio)], timeout=30
    )
    assert generated.returncode == 0, generated.stderr
    with ProjectRepository.create(
        tmp_path / "Project", "Tail", ProjectProfile(width=160, height=90, fps_numerator=25)
    ) as repository:
        editor, _ = _timeline(repository, paths, video)
        asset = AssetService(repository, MediaProbe(paths)).import_external(audio)
        track = editor.add_track(TrackKind.AUDIO, "Voice")
        editor.add_clip(track_id=track.id, asset_id=asset.id, timeline_start=0, source_in=0, duration=25)
        units = [
            SequenceBuildUnit(id="a", start_frame=0, end_frame=25),
            SequenceBuildUnit(id="b", start_frame=25, end_frame=50),
        ]
        service = SegmentedExportService(repository, paths)
        first = service.build(
            editor.state,
            _preset(audio_codec="aac"),
            units,
            repository.project_dir / "exports" / "first.mp4",
            overwrite=False,
        )
        second = service.build(
            editor.state,
            _preset(audio_codec="aac"),
            units,
            repository.project_dir / "exports" / "second.mp4",
            overwrite=False,
        )
        assert first.audio.status == "rendered" and second.audio.status == "reused"
        assert first.audio.output_path == second.audio.output_path
        probe = service.exporter.probe(first.audio.output_path)
        assert abs(float(probe["format"]["duration"]) - 2) < 0.08
        tail = FfmpegRunner(paths.ffmpeg).run(
            [
                "-ss",
                "1.25",
                "-i",
                str(first.audio.output_path),
                "-t",
                "0.5",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            timeout=30,
        )
        assert tail.returncode == 0, tail.stderr
        assert "max_volume: -91.0 dB" in tail.stderr


@pytest.mark.parametrize("explicit", [True, False])
def test_task_and_history_record_the_whole_subtitle_delivery(tmp_path: Path, monkeypatch, explicit):
    paths = replace(RuntimeContext.discover().paths, runtime_dir=configured_test_root() / "rt")
    source = tmp_path / "source.mp4"
    _video(source, paths, "red")
    repository = ProjectRepository.create(
        tmp_path / "Project",
        "Task subtitles",
        ProjectProfile(
            width=160,
            height=90,
            fps_numerator=25,
        ),
    )
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    try:
        editor, asset = _timeline(repository, paths, source)
        _subtitles(repository, editor, asset)
        monkeypatch.setattr("mediaflow.infrastructure.segmented_export_service.AUTO_SEGMENT_SECONDS", 1)
        args = dict(
            sequence_id=editor.sequence_id,
            output_path=str(repository.project_dir / "exports" / "task.mp4"),
            format=ExportFormat.H264,
            preset=_preset(),
        )
        if explicit:
            command = BuildSequenceCommand(
                **args,
                units=[
                    SequenceBuildUnit(id="a", start_frame=0, end_frame=25),
                    SequenceBuildUnit(id="b", start_frame=25, end_frame=50),
                ],
            )
        else:
            command = ExportSequenceCommand(**args)
        task = project.start_task(command, [asset.id], sequence_id=editor.sequence_id)
        completed = project.wait_for_task(task.id, timeout=60)
        assert completed.status == TaskStatus.COMPLETED, completed.error
        file_outcome = completed.outcome.output if explicit else completed.outcome.files[0]
        assert len(file_outcome.subtitle_files) == 1
        sidecar = file_outcome.subtitle_files[0].resolve(repository.project_dir)
        assert "SUBTITLE" in sidecar.read_text(encoding="utf-8-sig")
        assert sidecar in [artifact.resolve(repository.project_dir) for artifact in completed.artifacts]
        history = project.list_export_history(editor.sequence_id)[0]
        assert history.quality.subtitle_files == [str(sidecar)]
        assert repository.records.get_export_history(task.id).quality.subtitle_files == [str(sidecar)]
    finally:
        project.close()
