import subprocess
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import TaskStatus, TrackKind
from mediaflow.domain.sequence_audio import build_dialogue_transcription_plan
from mediaflow.domain.settings import AsrSettings, ServiceSettings
from mediaflow.domain.task_commands import TranscribeSequenceCommand
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def synthesize_real_speech(path: Path) -> None:
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{str(path).replace("'", "''")}'); "
        "$s.Speak('Hello world. This is a Media Flow transcription test.'); "
        "$s.Dispose()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    assert path.is_file() and path.stat().st_size > 1000


def test_real_faster_whisper_output_is_persisted_and_written_to_srt(tmp_path: Path) -> None:
    speech = tmp_path / "speech.wav"
    synthesize_real_speech(speech)
    paths = RuntimeContext.discover().paths
    repository = ProjectRepository.create(tmp_path / "Project", "Project")
    asset = AssetService(repository, MediaProbe(paths)).import_external(speech)
    settings = ServiceSettings(
        asr=AsrSettings(
            model="tiny.en",
            device="cpu",
            compute_type="int8",
            language="en",
        )
    )
    project = EditorProject(repository, settings=settings, paths=paths)
    try:
        sequence_id = repository.projects.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        audio_track = editor.add_track(TrackKind.AUDIO)
        editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )
        task = project.start_task(
            TranscribeSequenceCommand(
                plan=build_dialogue_transcription_plan(
                    repository.timeline.load_timeline(sequence_id),
                    {
                        item.id: item
                        for item in repository.assets.list_assets()
                    },
                    settings.asr,
                    project_profile=repository.sequences.get_sequence(
                        repository.projects.get_project().main_sequence_id
                    ).profile,
                )
            ),
            [asset.id],
            sequence_id=sequence_id,
        )
        completed = project.wait_for_task(task.id, timeout=180)
        documents = repository.subtitles.list_subtitle_documents(sequence_id=sequence_id)

        assert completed.status == TaskStatus.COMPLETED
        assert len(documents) == 1
        document = documents[0]
        segments = repository.subtitles.list_subtitle_segments(document.id)
        srt_files = list((repository.project_dir / "generated" / "subtitles").rglob("*.srt"))

        assert segments
        assert any("hello" in segment.text.lower() for segment in segments)
        assert srt_files
        assert "Hello" in srt_files[0].read_text(encoding="utf-8-sig")
    finally:
        project.close()
