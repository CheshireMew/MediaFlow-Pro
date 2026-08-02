from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import AssetKind, TaskStatus, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.sequence_audio import build_dialogue_transcription_plan
from mediaflow.domain.settings import AsrSettings, GlobalSettings
from mediaflow.domain.task_commands import TranscribeSequenceCommand
from mediaflow.infrastructure.asr_engine import (
    AsrPipeline,
    ChunkedAsrEngine,
    FasterWhisperCliEngine,
    FasterWhisperProcessEngine,
    create_asr_pipeline,
)
from mediaflow.infrastructure.asr_models import FasterWhisperModelStore
from mediaflow.infrastructure.audio_chunking import (
    AudioChunkingService,
    AudioPreparationService,
)
from mediaflow.infrastructure.cache_manager import CacheManager
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.runtime_tools import RuntimeToolService


def _runtime_paths(tmp_path: Path) -> RuntimePaths:
    installed = RuntimePaths.discover()
    return RuntimePaths(
        runtime_dir=tmp_path / "runtime",
        ffmpeg=installed.ffmpeg,
        ffprobe=installed.ffprobe,
        melt=installed.melt,
        native_qml=installed.native_qml,
    )


def _generate_audio(path: Path, paths: RuntimePaths, *, duration_seconds: int = 1) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration_seconds}",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _cache_runs(paths: RuntimePaths, category: str) -> list[Path]:
    runs = paths.runtime_dir / "cache" / category / "runs"
    return sorted(item for item in runs.iterdir()) if runs.is_dir() else []


def _transcription_command(
    repository: ProjectRepository,
    sequence_id: str,
    settings: AsrSettings,
) -> TranscribeSequenceCommand:
    state = repository.timeline.load_timeline(sequence_id)
    duration = state.duration_frames
    bounds = state.sequence.in_out
    plan = build_dialogue_transcription_plan(
        state,
        {asset.id: asset for asset in repository.catalog.list_assets()},
        settings,
        project_profile=repository.catalog.get_sequence(
            repository.catalog.get_project().main_sequence_id
        ).profile,
        start_frame=min(duration, bounds.in_frame) if bounds else 0,
        end_frame=min(duration, bounds.out_frame) if bounds else duration,
    )
    return TranscribeSequenceCommand(plan=plan)


def test_transcription_plan_uses_the_sequence_frame_clock(tmp_path: Path) -> None:
    main_profile = ProjectProfile(fps_numerator=25, fps_denominator=1)
    source = tmp_path / "clock-audio.wav"
    source.write_bytes(b"clock-audio")
    with ProjectRepository.create(
        tmp_path / "Transcription Clock",
        "Transcription Clock",
        main_profile,
    ) as repository:
        asset = repository.catalog.import_external_asset(source, AssetKind.AUDIO)
        asset = repository.catalog.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={"duration_frames": 25, "has_audio": True}
                    )
                }
            )
        )
        short = repository.catalog.create_short_sequence(
            "30 fps short",
            main_profile.model_copy(update={"fps_numerator": 30}),
        )
        editor = TimelineEditor(repository, short.id)
        audio_track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )

        plan = build_dialogue_transcription_plan(
            editor.state,
            {asset.id: asset},
            AsrSettings(),
            project_profile=main_profile,
        )
        assert len(plan.sources) == 1
        assert [
            (region.start_frame, region.end_frame)
            for region in plan.sources[0].regions
        ] == [(0, 30)]
        assert plan.recognition_seconds == 1.0


def test_built_in_pipeline_uses_the_shared_long_audio_chunk_orchestrator(
    tmp_path: Path,
) -> None:
    pipeline = create_asr_pipeline(
        AsrSettings(engine="builtin", model="tiny.en"),
        _runtime_paths(tmp_path),
    )

    assert isinstance(pipeline, AsrPipeline)
    assert isinstance(pipeline.engine, ChunkedAsrEngine)
    assert isinstance(pipeline.engine.engine, FasterWhisperProcessEngine)


def test_cli_engine_process_output_reaches_project_subtitles_and_srt(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "speech.wav"
    fake_cli = tmp_path / "fake_faster_whisper.py"
    model_root = tmp_path / "shared-models"
    installed_model = model_root / "faster-whisper-tiny.en"
    installed_model.mkdir(parents=True)
    (installed_model / "model.bin").write_bytes(b"installed model")
    _generate_audio(source, paths)
    fake_cli.write_text(
        """from pathlib import Path
import sys

output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
Path(__file__).with_name('observed-model-dir.txt').write_text(
    sys.argv[sys.argv.index('--model_dir') + 1],
    encoding='utf-8',
)
print('25%', flush=True)
(output / 'result.srt').write_text(
    '1\\n00:00:00,100 --> 00:00:00,900\\nCLI producer output\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
""",
        encoding="utf-8",
    )
    settings = AsrSettings(
        engine="faster_whisper_cli",
        cli_path=str(fake_cli),
        model_directory=str(model_root),
        model="tiny.en",
        device="cpu",
        language="en",
    )
    engine = create_asr_pipeline(settings, paths)
    assert isinstance(engine, AsrPipeline)
    assert isinstance(engine.engine, ChunkedAsrEngine)
    assert isinstance(engine.engine.engine, FasterWhisperCliEngine)
    progress: list[OperationProgress] = []

    repository = ProjectRepository.create(tmp_path / "Project", "Project")
    asset = AssetService(repository, MediaProbe(paths)).import_external(source)
    project = EditorProject(
        repository,
        settings=GlobalSettings(asr=settings),
        paths=paths,
    )
    try:
        sequence_id = repository.catalog.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        audio_track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        project.subscribe_task_events(
            lambda event: progress.append(
                OperationProgress.model_validate(event.payload["progress"])
            ),
            include_snapshot=False,
        )
        task = project.start_task(
            _transcription_command(repository, sequence_id, settings),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed = project.wait_for_task(task.id, timeout=10)
        documents = repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)
        assert completed.status == TaskStatus.COMPLETED
        assert len(documents) == 1
        document = documents[0]
        segments = repository.subtitles.list_subtitle_segments(document.id)
        words = repository.subtitles.list_subtitle_words(document.id)
        generated = list((repository.project_dir / "generated" / "subtitles").rglob("*.srt"))
        assert [segment.text for segment in segments] == ["CLI producer output"]
        assert (segments[0].start_frame, segments[0].end_frame) == (3, 27)
        assert [word.text for word in words] == ["CLI", "producer", "output"]
        assert all(word.timing_source == "estimated" for word in words)
        assert generated
        assert (tmp_path / "observed-model-dir.txt").read_text(encoding="utf-8") == str(
            model_root.resolve()
        )
        model_store = FasterWhisperModelStore(settings, paths)
        assert model_store.installed_models() == frozenset({"tiny.en"})
        assert model_store.local_model_path() == installed_model.resolve()
        assert model_store.builtin_model_reference() == str(installed_model.resolve())
        assert "CLI producer output" in generated[0].read_text(encoding="utf-8-sig")
        assert any(item.message_code == "transcribing" for item in progress)
        assert progress[-1].message_code == "completed"
        assert progress[-1].percent == 100.0
    finally:
        project.close()
    prewarm_progress: list[OperationProgress] = []
    warmed_cli = RuntimeToolService(GlobalSettings(asr=settings), paths).prewarm_cli(
        progress=prewarm_progress.append
    )
    assert warmed_cli == fake_cli.resolve()
    assert prewarm_progress[-1].message_code == "asr_cli_prewarming"
    assert prewarm_progress[-1].percent == 100.0


def test_runtime_tool_updates_versioned_ytdlp_and_installs_cli_on_runtime_drive(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    release_dir = tmp_path / "releases"
    release_dir.mkdir()
    wheel = release_dir / "yt_dlp-2099.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("yt_dlp/__init__.py", "from . import version\n")
        archive.writestr("yt_dlp/version.py", "__version__ = '2099.1'\n")
        archive.writestr("yt_dlp-2099.1.dist-info/METADATA", "Version: 2099.1\n")
    metadata = release_dir / "yt-dlp.json"
    metadata.write_text(
        json.dumps(
            {
                "info": {"version": "2099.1"},
                "urls": [
                    {
                        "filename": wheel.name,
                        "packagetype": "bdist_wheel",
                        "url": wheel.as_uri(),
                        "size": wheel.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cli_archive = release_dir / "fake-cli.7z"
    with zipfile.ZipFile(cli_archive, "w") as archive:
        archive.writestr(
            "Faster-Whisper-XXL/faster-whisper-xxl.exe",
            b"observable CLI artifact",
        )
    component_catalog = release_dir / "runtime-components.lock.json"
    component_catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "components": [
                    {
                        "id": "faster-whisper-xxl",
                        "display_name": "Faster-Whisper XXL",
                        "version": "test",
                        "homepage": "https://example.invalid/xxl",
                        "license": "MIT",
                        "archive": {
                            "file_name": cli_archive.name,
                            "url": cli_archive.as_uri(),
                            "size_bytes": cli_archive.stat().st_size,
                            "sha256": "",
                        },
                        "install": {
                            "root": "Faster-Whisper-XXL",
                            "entrypoint": "faster-whisper-xxl.exe",
                            "required_paths": ["faster-whisper-xxl.exe"],
                            "minimum_free_bytes": 0,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = GlobalSettings(asr=AsrSettings())
    service = RuntimeToolService(
        settings,
        paths,
        ytdlp_metadata_url=metadata.as_uri(),
        component_catalog_path=component_catalog,
    )
    progress: list[OperationProgress] = []
    updated = service.update_ytdlp(progress=progress.append)
    installed = service.install_components(
        ["faster-whisper-xxl"],
        progress=progress.append,
    )
    cli_path = Path(installed["faster-whisper-xxl"]) / "faster-whisper-xxl.exe"

    pointer = json.loads((paths.runtime_dir / "tools" / "yt-dlp-active.json").read_text(encoding="utf-8"))
    assert updated["version"] == "2099.1"
    assert pointer["version"] == "2099.1"
    assert Path(pointer["path"]).joinpath("yt_dlp", "version.py").is_file()
    assert service.ytdlp_version() == "2099.1"
    assert cli_path.read_bytes() == b"observable CLI artifact"
    assert paths.runtime_dir in cli_path.parents
    assert any(
        item.message_code == "runtime_component_downloading"
        and item.mode == "determinate"
        and item.unit == "bytes"
        for item in progress
    )
    assert progress[-1].message_code == "runtime_component_extracting"


def test_transcription_task_consumes_engine_and_smart_split_settings(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "speech.wav"
    fake_cli = tmp_path / "task_faster_whisper.py"
    _generate_audio(source, paths, duration_seconds=4)
    fake_cli.write_text(
        """from pathlib import Path
import sys

call_log = Path(__file__).with_name('task-calls.txt')
with call_log.open('a', encoding='utf-8') as handle:
    handle.write(sys.argv[sys.argv.index('--model') + 1] + '\\n')
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
(output / 'task.srt').write_text(
    '1\\n00:00:00,000 --> 00:00:04,000\\n这是一条需要按照设置进行智能拆分的很长字幕。\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
""",
        encoding="utf-8",
    )
    settings = GlobalSettings(
        asr=AsrSettings(
            engine="faster_whisper_cli",
            cli_path=str(fake_cli),
            model="tiny",
            device="cpu",
            language="zh",
            smart_split_limit=8,
        )
    )
    repository = ProjectRepository.create(tmp_path / "Task Project", "Task Project")
    asset = AssetService(repository, MediaProbe(paths)).import_external(source)
    project = EditorProject(repository, settings=settings, paths=paths)
    try:
        sequence_id = repository.catalog.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        audio_track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        command = _transcription_command(repository, sequence_id, settings.asr)
        settings.asr.model = "large-v3"
        task = project.start_task(
            command,
            [asset.id],
            sequence_id=sequence_id,
        )
        completed = project.wait_for_task(task.id, timeout=10)
        documents = repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)
        assert completed.status == TaskStatus.COMPLETED
        assert len(documents) == 1
        assert len(repository.subtitles.list_subtitle_segments(documents[0].id)) == 2
        subtitle_track = next(
            track for track in repository.timeline.load_timeline(sequence_id).tracks
            if track.kind == TrackKind.SUBTITLE
        )
        assert len(repository.subtitles.list_subtitle_placements(subtitle_track.id)) == 2
        removed_timeline_mix = repository.project_dir / "generated" / "audio" / (
            f"{sequence_id}-transcription.wav"
        )
        assert not removed_timeline_mix.exists()
        assert completed.artifacts
        assert completed.artifacts[0].resolve(repository.project_dir).is_file()
        assert documents[0].purpose == "sequence_transcript"
        assert (tmp_path / "task-calls.txt").read_text(
            encoding="utf-8"
        ).splitlines() == ["tiny"]

        repeated = project.start_task(
            _transcription_command(repository, sequence_id, command.plan.asr),
            [asset.id],
            sequence_id=sequence_id,
        )
        repeated_completed = project.wait_for_task(repeated.id, timeout=10)
        repeated_documents = repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)

        assert repeated_completed.status == TaskStatus.COMPLETED
        assert [document.id for document in repeated_documents] == [documents[0].id]
        assert len(repository.subtitles.list_subtitle_segments(documents[0].id)) == 2
        assert len(repository.subtitles.list_subtitle_placements(subtitle_track.id)) == 2
        assert (tmp_path / "task-calls.txt").read_text(
            encoding="utf-8"
        ).splitlines() == ["tiny"]
    finally:
        project.close()


def test_sequence_transcription_honors_in_out_and_offsets_persisted_subtitles(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "region-source.wav"
    fake_cli = tmp_path / "region_faster_whisper.py"
    _generate_audio(source, paths)
    fake_cli.write_text(
        """from pathlib import Path
import sys

source = Path(sys.argv[1])
assert source.is_file() and source.stat().st_size > 1000
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
(output / 'region.srt').write_text(
    '1\\n00:00:00,100 --> 00:00:00,500\\nRegion producer output\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
""",
        encoding="utf-8",
    )
    settings = GlobalSettings(
        asr=AsrSettings(
            engine="faster_whisper_cli",
            cli_path=str(fake_cli),
            model="tiny.en",
            device="cpu",
            language="en",
        )
    )
    repository = ProjectRepository.create(tmp_path / "Region Project", "Region Project")
    asset = AssetService(repository, MediaProbe(paths)).import_external(source)
    project = EditorProject(repository, settings=settings, paths=paths)
    try:
        sequence_id = repository.catalog.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        audio_track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        editor.set_sequence_in_out(6, 24)
        task = project.start_task(
            _transcription_command(repository, sequence_id, settings.asr),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed = project.wait_for_task(task.id, timeout=10)
        documents = repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)
        segments = repository.subtitles.list_subtitle_segments(documents[0].id)
        removed_timeline_mix = repository.project_dir / "generated" / "audio" / (
            f"{sequence_id}-transcription.wav"
        )

        assert completed.status == TaskStatus.COMPLETED
        assert len(documents) == 1
        assert [(item.start_frame, item.end_frame, item.text) for item in segments] == [
            (6, 15, "Region producer output")
        ]
        assert not removed_timeline_mix.exists()
        assert completed.artifacts
        assert completed.artifacts[0].resolve(repository.project_dir).is_file()
    finally:
        project.close()


def test_sequence_transcription_sends_each_unique_dialogue_source_once_and_maps_every_clip(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    first_source = tmp_path / "speaker-a.wav"
    second_source = tmp_path / "speaker-b.wav"
    fake_cli = tmp_path / "multi_source_faster_whisper.py"
    _generate_audio(first_source, paths, duration_seconds=2)
    _generate_audio(second_source, paths, duration_seconds=2)
    fake_cli.write_text(
        """from pathlib import Path
import sys

source = Path(sys.argv[1])
call_log = Path(__file__).with_name('multi-source-calls.txt')
calls = call_log.read_text(encoding='utf-8').splitlines() if call_log.exists() else []
label = 'source-' + str(len(calls) + 1)
with call_log.open('a', encoding='utf-8') as handle:
    handle.write(label + '\\n')
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
(output / 'source.srt').write_text(
    '1\\n00:00:00,100 --> 00:00:00,400\\n' + label + '\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
""",
        encoding="utf-8",
    )
    settings = GlobalSettings(
        asr=AsrSettings(
            engine="faster_whisper_cli",
            cli_path=str(fake_cli),
            model="tiny.en",
            device="cpu",
            language="en",
        )
    )
    repository = ProjectRepository.create(
        tmp_path / "Multi Source Project",
        "Multi Source Project",
    )
    assets = AssetService(repository, MediaProbe(paths))
    first_asset = assets.import_external(first_source)
    second_asset = assets.import_external(second_source)
    project = EditorProject(repository, settings=settings, paths=paths)
    try:
        sequence_id = repository.catalog.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        dialogue_track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=dialogue_track.id,
            asset_id=first_asset.id,
            timeline_start=0,
            source_in=0,
            duration=60,
        )
        editor.add_clip(
            track_id=dialogue_track.id,
            asset_id=second_asset.id,
            timeline_start=60,
            source_in=0,
            duration=60,
        )
        editor.add_clip(
            track_id=dialogue_track.id,
            asset_id=first_asset.id,
            timeline_start=120,
            source_in=3,
            duration=57,
        )

        task = project.start_task(
            _transcription_command(repository, sequence_id, settings.asr),
            [first_asset.id, second_asset.id],
            sequence_id=sequence_id,
        )
        completed = project.wait_for_task(task.id, timeout=10)

        assert completed.status == TaskStatus.COMPLETED
        document = next(
            item
            for item in repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)
            if item.purpose == "sequence_transcript"
        )
        assert [
            (item.start_frame, item.end_frame, item.text)
            for item in repository.subtitles.list_subtitle_segments(document.id)
        ] == [
            (3, 12, "source-1"),
            (63, 72, "source-2"),
            (120, 129, "source-1"),
        ]
        subtitle_track = next(
            track
            for track in repository.timeline.load_timeline(sequence_id).tracks
            if track.kind == TrackKind.SUBTITLE
        )
        assert [
            (item.start_frame, item.end_frame)
            for item in repository.subtitles.list_subtitle_placements(subtitle_track.id)
        ] == [(3, 12), (63, 72), (120, 129)]
        assert (tmp_path / "multi-source-calls.txt").read_text(
            encoding="utf-8"
        ).splitlines() == ["source-1", "source-2"]
        assert completed.artifacts[0].resolve(repository.project_dir).is_file()

        repeated = project.start_task(
            _transcription_command(repository, sequence_id, settings.asr),
            [first_asset.id, second_asset.id],
            sequence_id=sequence_id,
        )
        assert project.wait_for_task(repeated.id, timeout=10).status == TaskStatus.COMPLETED
        assert (tmp_path / "multi-source-calls.txt").read_text(
            encoding="utf-8"
        ).splitlines() == ["source-1", "source-2"]
    finally:
        project.close()


def test_sequence_transcription_extracts_only_merged_used_source_regions(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "long-interview.wav"
    fake_cli = tmp_path / "region_only_faster_whisper.py"
    _generate_audio(source, paths, duration_seconds=12)
    fake_cli.write_text(
        """from pathlib import Path
import sys

call_log = Path(__file__).with_name('region-only-calls.txt')
calls = call_log.read_text(encoding='utf-8').splitlines() if call_log.exists() else []
label = 'region-' + str(len(calls) + 1)
with call_log.open('a', encoding='utf-8') as handle:
    handle.write(label + '\\n')
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
(output / 'region.srt').write_text(
    '1\\n00:00:00,600 --> 00:00:00,900\\n' + label + '\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
""",
        encoding="utf-8",
    )
    settings = GlobalSettings(
        asr=AsrSettings(
            engine="faster_whisper_cli",
            cli_path=str(fake_cli),
            model="tiny.en",
            device="cpu",
            language="en",
        )
    )
    repository = ProjectRepository.create(
        tmp_path / "Used Regions Project",
        "Used Regions Project",
    )
    asset = AssetService(repository, MediaProbe(paths)).import_external(source)
    project = EditorProject(repository, settings=settings, paths=paths)
    try:
        sequence_id = repository.catalog.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        dialogue_track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=dialogue_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=60,
            duration=30,
        )
        editor.add_clip(
            track_id=dialogue_track.id,
            asset_id=asset.id,
            timeline_start=30,
            source_in=180,
            duration=30,
        )
        command = _transcription_command(repository, sequence_id, settings.asr)

        assert command.plan.source_count == 1
        assert command.plan.region_count == 2
        assert [
            (region.start_frame, region.end_frame)
            for region in command.plan.sources[0].regions
        ] == [(45, 105), (165, 225)]
        assert command.plan.recognition_seconds == 4.0

        completed = project.wait_for_task(
            project.start_task(
                command,
                [asset.id],
                sequence_id=sequence_id,
            ).id,
            timeout=10,
        )
        assert completed.status == TaskStatus.COMPLETED
        document = next(
            item
            for item in repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)
            if item.purpose == "sequence_transcript"
        )
        assert [
            (item.start_frame, item.end_frame, item.text)
            for item in repository.subtitles.list_subtitle_segments(document.id)
        ] == [
            (3, 12, "region-1"),
            (33, 42, "region-2"),
        ]
        prepared_inputs = list(
            (paths.runtime_dir / "cache" / "asr-inputs" / "runs").rglob("input.wav")
        )
        assert prepared_inputs == []
        assert (tmp_path / "region-only-calls.txt").read_text(
            encoding="utf-8"
        ).splitlines() == ["region-1", "region-2"]
    finally:
        project.close()


def test_long_audio_strategy_really_splits_files_and_offsets_cli_results(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "long-source.m4a"
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
            "sine=frequency=440:duration=2.5",
            "-c:a",
            "aac",
            str(source),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    fake_cli = tmp_path / "chunk_faster_whisper.py"
    fake_cli.write_text(
        """from pathlib import Path
import os
import sys
import time

source = Path(sys.argv[1])
assert source.is_file() and source.stat().st_size > 1000
marker = Path(__file__).with_name('active-' + str(os.getpid()))
marker.write_text('active', encoding='utf-8')
time.sleep(0.35)
if len(list(Path(__file__).parent.glob('active-*'))) > 1:
    Path(__file__).with_name('parallel-observed.txt').write_text('yes', encoding='utf-8')
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
(output / 'chunk.srt').write_text(
    '1\\n00:00:00,100 --> 00:00:00,400\\nChunk output\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
marker.unlink()
""",
        encoding="utf-8",
    )
    settings = AsrSettings(
        engine="faster_whisper_cli",
        cli_path=str(fake_cli),
        model="tiny.en",
        device="cpu",
        language="en",
        parallel_chunks=3,
    )
    progress: list[OperationProgress] = []
    engine = ChunkedAsrEngine(
        FasterWhisperCliEngine(settings, paths),
        settings,
        paths,
        threshold_seconds=2.0,
        target_chunk_seconds=1.0,
    )
    result = engine.transcribe(
        source,
        language="en",
        progress=progress.append,
    )
    chunk_files = list((paths.runtime_dir / "cache" / "asr-chunks" / "runs").rglob("*.wav"))

    assert chunk_files == []
    assert [round(segment.start_seconds, 1) for segment in result.segments] == [0.1, 1.1, 2.1]
    assert [round(segment.end_seconds, 1) for segment in result.segments] == [0.4, 1.4, 2.4]
    assert any(item.message_code == "asr_silence_detection" for item in progress)
    assert any(item.message_code == "asr_chunk_extracting" for item in progress)
    assert any(item.message_code == "asr_chunks_transcribing" for item in progress)
    assert progress[-1].message_code == "asr_chunks_transcribing"
    assert progress[-1].completed == progress[-1].total
    assert (tmp_path / "parallel-observed.txt").read_text(encoding="utf-8") == "yes"


def test_xxl_task_chain_chunks_and_parallelizes_real_long_audio(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "long-task-source.m4a"
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
            "sine=frequency=440:duration=901",
            "-c:a",
            "aac",
            "-b:a",
            "24k",
            str(source),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    fake_cli = tmp_path / "long_task_faster_whisper.py"
    fake_cli.write_text(
        """from pathlib import Path
import os
import sys
import time

root = Path(__file__).parent
Path(root / ('call-' + str(os.getpid()) + '.txt')).write_text(
    Path(sys.argv[1]).name,
    encoding='utf-8',
)
active = root / ('task-active-' + str(os.getpid()))
active.write_text('active', encoding='utf-8')
time.sleep(0.35)
if len(list(root.glob('task-active-*'))) > 1:
    (root / 'task-parallel-observed.txt').write_text('yes', encoding='utf-8')
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
(output / 'chunk.srt').write_text(
    '1\\n00:00:00,100 --> 00:00:00,400\\nLong chunk\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
active.unlink()
""",
        encoding="utf-8",
    )
    settings = GlobalSettings(
        asr=AsrSettings(
            engine="faster_whisper_cli",
            cli_path=str(fake_cli),
            model="tiny.en",
            device="cpu",
            language="en",
            parallel_chunks=2,
        )
    )
    repository = ProjectRepository.create(
        tmp_path / "Long XXL Task",
        "Long XXL Task",
    )
    asset = AssetService(repository, MediaProbe(paths)).import_external(source)
    project = EditorProject(repository, settings=settings, paths=paths)
    try:
        task_progress: list[OperationProgress] = []
        project.subscribe_task_events(
            lambda event: task_progress.append(
                OperationProgress.model_validate(event.payload["progress"])
            ),
            include_snapshot=False,
        )
        sequence_id = repository.catalog.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        completed = project.wait_for_task(
            project.start_task(
                _transcription_command(repository, sequence_id, settings.asr),
                [asset.id],
                sequence_id=sequence_id,
            ).id,
            timeout=60,
        )

        assert completed.status == TaskStatus.COMPLETED
        assert len(list(tmp_path.glob("call-*.txt"))) == 2
        assert (
            tmp_path / "task-parallel-observed.txt"
        ).read_text(encoding="utf-8") == "yes"
        document = next(
            item
            for item in repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)
            if item.purpose == "sequence_transcript"
        )
        segments = repository.subtitles.list_subtitle_segments(document.id)
        assert len(segments) == 2
        assert [segment.text for segment in segments] == [
            "Long chunk",
            "Long chunk",
        ]
        assert segments[0].start_frame < 30
        assert segments[1].start_frame > 17_000
        overall_values = [
            progress.overall_percent
            for progress in task_progress
            if progress.overall_percent is not None
        ]
        assert overall_values
        assert overall_values == sorted(overall_values)
        assert overall_values[-1] == 100.0
        assert any(
            progress.mode == "indeterminate"
            and progress.message_code == "asr_cli_starting"
            and progress.overall_percent is not None
            for progress in task_progress
        )
    finally:
        project.close()


def test_audio_preparation_preserves_strongly_antiphase_stereo_signal(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "antiphase.wav"
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
            "aevalsrc=sin(2*PI*440*t)|-sin(2*PI*440*t):s=48000:d=1",
            str(source),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")

    prepared = AudioPreparationService(paths).prepare_for_asr(source)
    try:
        probe = subprocess.run(
            [
                str(paths.ffprobe),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=channels,sample_rate,codec_name",
                "-of",
                "json",
                str(prepared),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert probe.returncode == 0, probe.stderr
        stream = json.loads(probe.stdout)["streams"][0]
        assert stream == {
            "codec_name": "pcm_s16le",
            "sample_rate": "16000",
            "channels": 1,
        }

        volume = subprocess.run(
            [
                str(paths.ffmpeg),
                "-hide_banner",
                "-i",
                str(prepared),
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert volume.returncode == 0, volume.stderr
        match = __import__("re").search(
            r"mean_volume:\s*(-?[0-9.]+) dB",
            volume.stderr,
        )
        assert match and float(match.group(1)) > -10.0
    finally:
        CacheManager(paths.runtime_dir / "cache").cleanup_run(prepared.parent)


def test_audio_preparation_probe_failure_does_not_allocate_a_cache_run(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "not-media.bin"
    source.write_bytes(b"not a media stream")

    with pytest.raises(RuntimeError):
        AudioPreparationService(paths).prepare_for_asr(source)

    assert _cache_runs(paths, "asr-inputs") == []


def test_audio_preparation_encoding_failure_removes_its_cache_run(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "source.wav"
    _generate_audio(source, paths)
    broken_paths = RuntimePaths(
        runtime_dir=paths.runtime_dir,
        ffmpeg=paths.ffprobe,
        ffprobe=paths.ffprobe,
        melt=paths.melt,
        native_qml=paths.native_qml,
    )

    with pytest.raises(RuntimeError, match="准备 ASR 音频失败"):
        AudioPreparationService(broken_paths).prepare_for_asr(source)

    assert _cache_runs(paths, "asr-inputs") == []


def test_audio_preparation_cancellation_removes_its_cache_run(
    tmp_path: Path,
) -> None:
    class Cancelled(BaseException):
        pass

    paths = _runtime_paths(tmp_path)
    source = tmp_path / "source.wav"
    _generate_audio(source, paths, duration_seconds=3)
    observed_run = False

    def cancel_after_process_start() -> None:
        nonlocal observed_run
        observed_run = bool(_cache_runs(paths, "asr-inputs"))
        raise Cancelled

    with pytest.raises(Cancelled):
        AudioPreparationService(paths).prepare_for_asr(
            source,
            check_cancelled=cancel_after_process_start,
        )

    assert observed_run
    assert _cache_runs(paths, "asr-inputs") == []


def test_audio_chunking_mid_run_failure_removes_all_generated_chunks(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "source.wav"
    _generate_audio(source, paths, duration_seconds=3)
    observed_first_chunk = False

    def fail_during_second_chunk(progress: OperationProgress) -> None:
        nonlocal observed_first_chunk
        if progress.message_code != "asr_chunk_extracting":
            return
        observed_first_chunk = any(
            run.joinpath("chunk-000.wav").is_file()
            for run in _cache_runs(paths, "asr-chunks")
        )
        if progress.completed is not None and progress.completed > 1.0:
            raise RuntimeError("injected second chunk failure")

    with pytest.raises(RuntimeError, match="injected second chunk failure"):
        AudioChunkingService(paths).extract_chunks(
            source,
            [1.0, 2.0],
            total_duration=3.0,
            progress=fail_during_second_chunk,
        )

    assert observed_first_chunk
    assert _cache_runs(paths, "asr-chunks") == []
