from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import pytest

from mediaflow.domain.dubbing import DiarizationSpeechInterval
from mediaflow.domain.settings import SpeakerDiarizationSettings
from mediaflow.infrastructure.speaker_diarization import (
    PyannoteDiarizationEngine,
    TranscriptSpeakerClusteringEngine,
    pyannote_model_ready_marker,
)


def _wave(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * 16_000)
    return path


def test_pyannote_engine_keeps_caches_with_its_isolated_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    environment_root = tmp_path / "pyannote"
    python = environment_root / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    source = _wave(tmp_path / "dialogue.wav")
    observed_environment: dict[str, str] = {}

    def run(command, *, env, **_arguments):
        observed_environment.update(env)
        output = Path(command[command.index("--output") + 1])
        output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "engine": "pyannote.audio",
                    "engine_version": "4.0.7",
                    "model": "pyannote/speaker-diarization-community-1",
                    "device": "cuda",
                    "exclusive": True,
                    "turns": [
                        {
                            "speaker": "SPEAKER_00",
                            "start_seconds": 0.0,
                            "end_seconds": 1.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "mediaflow.infrastructure.speaker_diarization.run_cancellable",
        run,
    )
    settings = SpeakerDiarizationSettings(
        python_executable=str(python),
        hugging_face_token="test-token",
    )

    result = PyannoteDiarizationEngine(settings).diarize(source)

    cache_root = environment_root / "cache"
    assert result.exclusive is True
    assert observed_environment["HF_HOME"] == str(cache_root / "huggingface")
    assert observed_environment["HF_HUB_CACHE"] == str(
        cache_root / "huggingface" / "hub"
    )
    assert observed_environment["TORCH_HOME"] == str(cache_root / "torch")
    assert observed_environment["HF_TOKEN"] == "test-token"
    assert pyannote_model_ready_marker(python, settings.model).is_file()


def test_transcript_clustering_uses_exact_non_overlapping_transcript_intervals(
    tmp_path: Path,
    monkeypatch,
) -> None:
    python = tmp_path / "speaker" / "Scripts" / "python.exe"
    model = tmp_path / "speaker" / "campplus.onnx"
    python.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    model.touch()
    source = _wave(tmp_path / "dialogue.wav")
    captured_request: dict = {}

    def run(command, **_arguments):
        request = Path(command[command.index("--request") + 1])
        captured_request.update(json.loads(request.read_text(encoding="utf-8")))
        output = Path(command[command.index("--output") + 1])
        output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "engine": "3D-Speaker CAM++ via sherpa-onnx",
                    "engine_version": "1.13.5",
                    "model": model.name,
                    "device": "cpu",
                    "exclusive": True,
                    "turns": [
                        {
                            "speaker": "SPEAKER_00",
                            "start_seconds": 0.0,
                            "end_seconds": 0.4,
                        },
                        {
                            "speaker": "SPEAKER_01",
                            "start_seconds": 0.6,
                            "end_seconds": 1.0,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "mediaflow.infrastructure.speaker_diarization.run_cancellable",
        run,
    )
    intervals = (
        DiarizationSpeechInterval(start_seconds=0.0, end_seconds=0.4),
        DiarizationSpeechInterval(start_seconds=0.6, end_seconds=1.0),
    )
    settings = SpeakerDiarizationSettings(
        clustering_python_executable=str(python),
        embedding_model_path=str(model),
    )

    result = TranscriptSpeakerClusteringEngine(settings).diarize(
        source,
        speech_intervals=intervals,
        minimum_speakers=2,
        maximum_speakers=2,
    )

    assert result.engine == "3D-Speaker CAM++ via sherpa-onnx"
    assert [item.speaker for item in result.turns] == ["SPEAKER_00", "SPEAKER_01"]
    assert captured_request["intervals"] == [
        {"start_seconds": 0.0, "end_seconds": 0.4},
        {"start_seconds": 0.6, "end_seconds": 1.0},
    ]


def test_transcript_clustering_rejects_overlapping_intervals(tmp_path: Path) -> None:
    python = tmp_path / "speaker" / "Scripts" / "python.exe"
    model = tmp_path / "speaker" / "campplus.onnx"
    python.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    model.touch()
    source = _wave(tmp_path / "dialogue.wav")
    settings = SpeakerDiarizationSettings(
        clustering_python_executable=str(python),
        embedding_model_path=str(model),
    )

    with pytest.raises(ValueError, match="Community-1"):
        TranscriptSpeakerClusteringEngine(settings).diarize(
            source,
            speech_intervals=(
                DiarizationSpeechInterval(start_seconds=0.0, end_seconds=0.7),
                DiarizationSpeechInterval(start_seconds=0.6, end_seconds=1.0),
            ),
        )
