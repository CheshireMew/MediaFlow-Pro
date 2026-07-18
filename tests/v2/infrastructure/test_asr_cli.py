from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

from mediaflow.application.asset_service import AssetService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.settings import AsrSettings, GlobalSettings
from mediaflow.domain.task_commands import TranscribeAssetCommand, TranscribeRegionCommand
from mediaflow.infrastructure.asr_engine import (
    FasterWhisperCliEngine,
    LongAudioAsrEngine,
    PreparedAudioAsrEngine,
    create_asr_engine,
)
from mediaflow.infrastructure.audio_chunking import AudioPreparationService
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


def _generate_audio(path: Path, paths: RuntimePaths) -> None:
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
            "sine=frequency=440:duration=1",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_cli_engine_process_output_reaches_project_subtitles_and_srt(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "speech.wav"
    fake_cli = tmp_path / "fake_faster_whisper.py"
    _generate_audio(source, paths)
    fake_cli.write_text(
        """from pathlib import Path
import sys

output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
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
        model="tiny.en",
        device="cpu",
        language="en",
    )
    engine = create_asr_engine(settings, paths)
    assert isinstance(engine, PreparedAudioAsrEngine)
    assert isinstance(engine.engine, FasterWhisperCliEngine)
    progress: list[tuple[float, str]] = []

    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        publication = SubtitlePublicationService(repository)
        document = SubtitleAcquisitionService(repository, publication).transcribe_asset(
            asset.id,
            engine,
            progress=lambda value, code: progress.append((value, code)),
        )
        segments = repository.list_subtitle_segments(document.id)
        generated = list((repository.project_dir / "generated" / "subtitles").rglob("*.srt"))
        assert [segment.text for segment in segments] == ["CLI producer output"]
        assert (segments[0].start_frame, segments[0].end_frame) == (3, 27)
        assert generated
        assert "CLI producer output" in generated[0].read_text(encoding="utf-8-sig")
        assert any(value >= 95 and code == "transcribing" for value, code in progress)
        assert progress[-1] == (100.0, "transcription_completed")
    prewarm_progress: list[tuple[float, str]] = []
    warmed_cli = RuntimeToolService(settings, paths).prewarm_cli(
        progress=lambda value, code: prewarm_progress.append((value, code))
    )
    assert warmed_cli == fake_cli.resolve()
    assert prewarm_progress[-1] == (100, "asr_cli_prewarmed")


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
    settings = AsrSettings()
    service = RuntimeToolService(
        settings,
        paths,
        ytdlp_metadata_url=metadata.as_uri(),
        cli_url=cli_archive.as_uri(),
        cli_archive=cli_archive.name,
        cli_size=cli_archive.stat().st_size,
    )
    progress: list[tuple[float, str]] = []
    updated = service.update_ytdlp(progress=lambda value, code: progress.append((value, code)))
    cli_path = service.install_faster_whisper_cli(progress=lambda value, code: progress.append((value, code)))

    pointer = json.loads((paths.runtime_dir / "tools" / "yt-dlp-active.json").read_text(encoding="utf-8"))
    assert updated["version"] == "2099.1"
    assert pointer["version"] == "2099.1"
    assert Path(pointer["path"]).joinpath("yt_dlp", "version.py").is_file()
    assert service.ytdlp_version() == "2099.1"
    assert cli_path.read_bytes() == b"observable CLI artifact"
    assert paths.runtime_dir in cli_path.parents
    assert progress[-1] == (100, "asr_cli_installed")


def test_transcription_task_consumes_engine_and_smart_split_settings(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    source = tmp_path / "speech.wav"
    fake_cli = tmp_path / "task_faster_whisper.py"
    _generate_audio(source, paths)
    fake_cli.write_text(
        """from pathlib import Path
import sys

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
        task = project.start_task(
            TranscribeAssetCommand(asset_id=asset.id),
            [asset.id],
        )
        completed = project.tasks.wait(task.id, timeout=10)
        documents = repository.list_subtitle_documents(asset.id)
        assert completed.status == TaskStatus.COMPLETED
        assert len(documents) == 1
        assert len(repository.list_subtitle_segments(documents[0].id)) == 2
        assert completed.artifacts
        assert (repository.project_dir / completed.artifacts[0]).is_file()
    finally:
        project.close()


def test_region_transcription_task_extracts_audio_and_offsets_persisted_subtitles(
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
        task = project.start_task(
            TranscribeRegionCommand(
                asset_id=asset.id,
                start_frame=6,
                end_frame=24,
                document_id="",
                translate_after=False,
            ),
            [asset.id],
        )
        completed = project.tasks.wait(task.id, timeout=10)
        documents = repository.list_subtitle_documents(asset.id)
        segments = repository.list_subtitle_segments(documents[0].id)
        region_audio = list((repository.project_dir / "cache" / "asr-regions").glob("*.wav"))

        assert completed.status == TaskStatus.COMPLETED
        assert len(documents) == 1
        assert [(item.start_frame, item.end_frame, item.text) for item in segments] == [
            (9, 21, "Region producer output")
        ]
        assert region_audio and region_audio[0].stat().st_size > 1000
        assert completed.artifacts
        assert (repository.project_dir / completed.artifacts[0]).is_file()
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
import sys

source = Path(sys.argv[1])
assert source.is_file() and source.stat().st_size > 1000
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
(output / 'chunk.srt').write_text(
    '1\\n00:00:00,100 --> 00:00:00,400\\nChunk output\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
""",
        encoding="utf-8",
    )
    settings = AsrSettings(
        engine="faster_whisper_cli",
        cli_path=str(fake_cli),
        model="tiny.en",
        device="cpu",
        language="en",
    )
    progress: list[tuple[float, str]] = []
    engine = LongAudioAsrEngine(
        FasterWhisperCliEngine(settings, paths),
        paths,
        threshold_seconds=2.0,
        target_chunk_seconds=1.0,
    )
    result = engine.transcribe(
        source,
        language="en",
        progress=lambda value, code: progress.append((value, code)),
    )
    chunk_files = list((paths.runtime_dir / "cache" / "asr-chunks" / "runs").rglob("*.wav"))

    assert len(chunk_files) == 3
    assert all(path.stat().st_size > 1000 for path in chunk_files)
    assert [round(segment.start_seconds, 1) for segment in result.segments] == [0.1, 1.1, 2.1]
    assert [round(segment.end_seconds, 1) for segment in result.segments] == [0.4, 1.4, 2.4]
    assert any(code == "asr_audio_splitting" for _, code in progress)
    assert any(code == "asr_chunks_progress" for _, code in progress)
    assert progress[-1] == (100.0, "transcription_completed")


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
    assert stream == {"codec_name": "pcm_s16le", "sample_rate": "16000", "channels": 1}

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
    match = __import__("re").search(r"mean_volume:\s*(-?[0-9.]+) dB", volume.stderr)
    assert match and float(match.group(1)) > -10.0
