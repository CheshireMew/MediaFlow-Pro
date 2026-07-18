from __future__ import annotations

import subprocess
from pathlib import Path

from mediaflow.application.asset_service import AssetService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.domain.settings import AsrSettings
from mediaflow.infrastructure.asr_engine import FasterWhisperProcessEngine
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths


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
    paths = RuntimePaths.discover()
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(speech)
        settings = AsrSettings(
            model="tiny.en",
            device="cpu",
            compute_type="int8",
            language="en",
        )
        publication = SubtitlePublicationService(repository)
        document = SubtitleAcquisitionService(repository, publication).transcribe_asset(
            asset.id,
            FasterWhisperProcessEngine(settings, paths),
            language="en",
        )
        segments = repository.list_subtitle_segments(document.id)
        srt_files = list((repository.project_dir / "generated" / "subtitles").rglob("*.srt"))

        assert segments
        assert any("hello" in segment.text.lower() for segment in segments)
        assert srt_files
        assert "Hello" in srt_files[0].read_text(encoding="utf-8-sig")
