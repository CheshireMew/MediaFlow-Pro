from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from mediaflow.domain.voiceover import VoiceoverLatencyCalibration

CALIBRATION_SAMPLE_RATE = 48_000
CALIBRATION_MARKER_OFFSET_SAMPLES = 12_000
CALIBRATION_MARKER_LENGTH_SAMPLES = 5_760
CALIBRATION_CAPTURE_SECONDS = 2.0


def calibration_marker(*, sample_rate: int = CALIBRATION_SAMPLE_RATE) -> np.ndarray:
    """Return the deterministic broadband marker used for acoustic calibration."""
    length = round(CALIBRATION_MARKER_LENGTH_SAMPLES * sample_rate / CALIBRATION_SAMPLE_RATE)
    generator = np.random.default_rng(0x4D4650)
    signal = generator.choice(np.asarray([-1.0, 1.0]), size=length).astype(np.float64)
    edge = max(1, round(sample_rate * 0.005))
    envelope = np.ones(length, dtype=np.float64)
    envelope[:edge] = np.linspace(0.0, 1.0, edge, endpoint=False)
    envelope[-edge:] = np.linspace(1.0, 0.0, edge, endpoint=False)
    return signal * envelope * 0.35


def write_calibration_signal(
    destination: str | Path,
    *,
    sample_rate: int = CALIBRATION_SAMPLE_RATE,
) -> Path:
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    marker_offset = round(CALIBRATION_MARKER_OFFSET_SAMPLES * sample_rate / CALIBRATION_SAMPLE_RATE)
    total = round(CALIBRATION_CAPTURE_SECONDS * sample_rate)
    samples = np.zeros(total, dtype=np.float64)
    marker = calibration_marker(sample_rate=sample_rate)
    samples[marker_offset : marker_offset + len(marker)] = marker
    payload = np.clip(samples * 32767.0, -32768, 32767).astype("<i2").tobytes()
    with wave.open(str(output), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(payload)
    return output


def analyze_latency_recording(
    recording: str | Path,
    *,
    device_id: str,
    device_name: str,
    marker_offset_samples: int = CALIBRATION_MARKER_OFFSET_SAMPLES,
    max_latency_ms: int = 1_000,
) -> VoiceoverLatencyCalibration:
    source = Path(recording).expanduser().resolve(strict=True)
    samples, sample_rate = _read_pcm_wave(source)
    marker = calibration_marker(sample_rate=sample_rate)
    scaled_offset = round(marker_offset_samples * sample_rate / CALIBRATION_SAMPLE_RATE)
    search_end = min(
        len(samples) - len(marker) + 1,
        scaled_offset + round(max_latency_ms * sample_rate / 1000),
    )
    if search_end <= scaled_offset:
        raise ValueError("校准录音太短，未覆盖延迟搜索范围")
    correlations = _normalized_valid_correlations(samples, marker)
    search = correlations[scaled_offset:search_end]
    if not len(search):
        raise ValueError("校准录音中没有可分析的标记区间")
    relative = int(np.argmax(np.abs(search)))
    score = float(abs(search[relative]))
    if not np.isfinite(score) or score < 0.18:
        raise ValueError("没有识别到可靠的校准声，请调高扬声器音量并重试")
    peak = scaled_offset + relative
    latency_samples = max(0, peak - scaled_offset)
    return VoiceoverLatencyCalibration(
        device_id=device_id,
        device_name=device_name,
        latency_samples=latency_samples,
        sample_rate=sample_rate,
        confidence=min(1.0, score),
        method="acoustic_roundtrip",
    )


def _read_pcm_wave(source: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(source), "rb") as audio:
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()
        sample_rate = audio.getframerate()
        frames = audio.readframes(audio.getnframes())
    if sample_width != 2:
        raise ValueError("延迟校准仅支持 16-bit PCM WAV 录音")
    values = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    if channels > 1:
        values = values.reshape(-1, channels).mean(axis=1)
    return values, sample_rate


def _normalized_valid_correlations(
    samples: np.ndarray,
    marker: np.ndarray,
) -> np.ndarray:
    size = len(samples) + len(marker) - 1
    fft_size = 1 << max(1, size - 1).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(samples, fft_size) * np.fft.rfft(marker[::-1], fft_size),
        fft_size,
    )[:size]
    correlations = convolution[len(marker) - 1 : len(samples)]
    squared = np.square(samples)
    cumulative = np.concatenate((np.asarray([0.0]), np.cumsum(squared)))
    window_energy = cumulative[len(marker) :] - cumulative[: -len(marker)]
    denominator = np.sqrt(window_energy * float(np.dot(marker, marker)))
    return np.divide(
        correlations,
        denominator,
        out=np.zeros_like(correlations),
        where=denominator > 1e-12,
    )
