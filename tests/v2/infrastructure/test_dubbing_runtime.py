from __future__ import annotations

import wave
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from mediaflow.application.ports import ProjectTaskDocuments
from mediaflow.infrastructure.dubbing_runtime import InfrastructureDubbingRuntime
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.resources.pyannote_diarize import _load_pcm_wave


def _wave(path: Path, duration_seconds: float, sample_rate: int = 48_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0" * round(duration_seconds * sample_rate))
    return path


def test_ffmpeg_normalizes_lines_and_places_them_in_one_master(
    tmp_path: Path,
) -> None:
    runtime = InfrastructureDubbingRuntime(
        cast(ProjectTaskDocuments, object()),
        RuntimeContext.discover().paths,
    )
    first = _wave(tmp_path / "first.wav", 1.0)
    second = _wave(tmp_path / "second.wav", 1.0)
    normalized = runtime.normalize_utterance(
        first,
        tmp_path / "normalized.wav",
        target_seconds=2.0,
        sample_rate=48_000,
        check_cancelled=lambda: None,
    )
    assert normalized.duration_seconds == pytest.approx(2.0, abs=0.01)

    master = runtime.assemble_master(
        [(normalized.path, 0.0), (second, 2.5)],
        tmp_path / "master.wav",
        minimum_duration_seconds=3.0,
        sample_rate=48_000,
        check_cancelled=lambda: None,
    )
    assert master.duration_seconds == pytest.approx(3.5, abs=0.02)
    assert master.sample_rate == 48_000
    assert master.channels == 1


def test_pyannote_sidecar_loads_mediaflow_pcm_without_torchcodec(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dialogue.wav"
    samples = np.asarray([-32768, -16384, 0, 16384, 32767], dtype="<i2")
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(samples.tobytes())

    class _Torch:
        @staticmethod
        def from_numpy(value):
            return value

    decoded = _load_pcm_wave(source, np, _Torch)

    assert decoded["sample_rate"] == 16_000
    waveform = decoded["waveform"]
    assert waveform.shape == (1, 5)
    assert waveform[0, 0] == pytest.approx(-1.0)
    assert waveform[0, 3] == pytest.approx(0.5)
