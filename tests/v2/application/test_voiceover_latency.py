from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from mediaflow.application.voiceover_latency import (
    CALIBRATION_MARKER_OFFSET_SAMPLES,
    analyze_latency_recording,
    write_calibration_signal,
)


def test_acoustic_latency_analysis_recovers_known_sample_delay(tmp_path: Path) -> None:
    emitted = write_calibration_signal(tmp_path / "emitted.wav")
    with wave.open(str(emitted), "rb") as source:
        parameters = source.getparams()
        payload = source.readframes(source.getnframes())
    known_delay = 3_840
    recording = tmp_path / "recorded.wav"
    with wave.open(str(recording), "wb") as output:
        output.setparams(parameters)
        output.writeframes(np.zeros(known_delay, dtype="<i2").tobytes() + payload)

    calibration = analyze_latency_recording(
        recording,
        device_id="test-device",
        device_name="Test microphone",
        marker_offset_samples=CALIBRATION_MARKER_OFFSET_SAMPLES,
    )

    assert calibration.latency_samples == known_delay
    assert calibration.sample_rate == 48_000
    assert calibration.method == "acoustic_roundtrip"
    assert calibration.confidence > 0.99
