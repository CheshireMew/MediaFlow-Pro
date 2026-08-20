from __future__ import annotations

import argparse
import json
import os
import warnings
import wave
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _annotation_rows(annotation) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if hasattr(annotation, "itertracks"):
        values = annotation.itertracks(yield_label=True)
        for turn, _track, speaker in values:
            rows.append(
                {
                    "speaker": str(speaker),
                    "start_seconds": float(turn.start),
                    "end_seconds": float(turn.end),
                }
            )
        return rows
    for value in annotation:
        if len(value) == 2:
            turn, speaker = value
        else:
            turn, _track, speaker = value
        rows.append(
            {
                "speaker": str(speaker),
                "start_seconds": float(turn.start),
                "end_seconds": float(turn.end),
            }
        )
    return rows


def _load_pcm_wave(path: str | Path, numpy, torch) -> dict[str, object]:
    """Load the PCM WAV produced by MediaFlow without relying on TorchCodec."""

    source = Path(path).resolve(strict=True)
    try:
        with wave.open(str(source), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            compression = audio.getcomptype()
            payload = audio.readframes(frames)
    except (OSError, wave.Error) as error:
        raise RuntimeError(f"无法读取 MediaFlow 生成的对白 WAV：{error}") from error
    if compression != "NONE" or sample_width != 2:
        raise RuntimeError("说话人识别只接受 MediaFlow 生成的 16-bit PCM WAV")
    if channels <= 0 or sample_rate <= 0 or frames <= 0:
        raise RuntimeError("说话人识别输入 WAV 没有有效音频帧")
    samples = numpy.frombuffer(payload, dtype="<i2")
    if samples.size != frames * channels:
        raise RuntimeError("说话人识别输入 WAV 的音频帧不完整")
    waveform = (
        samples.reshape(frames, channels)
        .T.astype(numpy.float32, copy=True)
        / 32768.0
    )
    return {
        "waveform": torch.from_numpy(waveform),
        "sample_rate": sample_rate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--minimum-speakers", type=int)
    parser.add_argument("--maximum-speakers", type=int)
    arguments = parser.parse_args()

    import numpy
    import torch

    warnings.filterwarnings(
        "ignore",
        message=r"\s*torchcodec is not installed correctly",
        category=UserWarning,
    )
    from pyannote.audio import Pipeline

    token = os.environ.get("HF_TOKEN", "").strip() or None
    try:
        pipeline = Pipeline.from_pretrained(arguments.model, token=token)
    except TypeError:
        pipeline = Pipeline.from_pretrained(arguments.model, use_auth_token=token)
    selected_device = arguments.device
    if selected_device == "auto":
        selected_device = "cuda" if torch.cuda.is_available() else "cpu"
    pipeline.to(torch.device(selected_device))
    options: dict[str, int] = {}
    if arguments.minimum_speakers is not None:
        options["min_speakers"] = arguments.minimum_speakers
    if arguments.maximum_speakers is not None:
        options["max_speakers"] = arguments.maximum_speakers
    output = pipeline(_load_pcm_wave(arguments.input, numpy, torch), **options)
    exclusive = getattr(output, "exclusive_speaker_diarization", None)
    regular = getattr(output, "speaker_diarization", output)
    annotation = exclusive if exclusive is not None else regular
    try:
        package_version = version("pyannote.audio")
    except PackageNotFoundError:
        package_version = "unknown"
    payload = {
        "schema_version": 1,
        "engine": "pyannote.audio",
        "engine_version": package_version,
        "model": arguments.model,
        "device": selected_device,
        "exclusive": exclusive is not None,
        "turns": _annotation_rows(annotation),
    }
    destination = Path(arguments.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
