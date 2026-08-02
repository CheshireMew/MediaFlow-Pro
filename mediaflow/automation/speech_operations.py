from __future__ import annotations

import hashlib
from pathlib import Path

from mediaflow.automation.operation_context import OperationContext
from mediaflow.automation.operation_models import (
    SpeechSegmentResult,
    SpeechSynthesisResult,
    SpeechTranscriptionResult,
)
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.infrastructure.asr_engine import FasterWhisperCliEngine
from mediaflow.infrastructure.gpt_sovits_engine import GptSoVitsEngine
from mediaflow.infrastructure.output_reservation import reserve_python_output
from mediaflow.infrastructure.runtime_components import RuntimeComponentService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.settings_repository import SettingsRepository


def transcribe(context: OperationContext) -> SpeechTranscriptionResult:
    settings, paths = _runtime(context)
    values = context.arguments
    source = Path(str(values["input_path"])).expanduser().resolve(strict=True)
    output = Path(str(values["output_path"])).expanduser().resolve()
    if output.suffix.lower() != ".srt":
        raise ValueError("speech.transcribe 的 output_path 必须是 .srt")
    if output.exists() and not bool(values.get("overwrite", False)):
        raise FileExistsError(f"输出已存在：{output}")
    components = RuntimeComponentService(settings, paths)
    installation = components.resolve("faster-whisper-xxl")
    if installation is None:
        raise FileNotFoundError("请先安装或选择 Faster-Whisper XXL")
    updates = {"cli_path": str(installation.entrypoint)}
    for argument, setting_name in (
        ("model", "model"),
        ("device", "device"),
        ("compute_type", "compute_type"),
    ):
        if values.get(argument) is not None:
            updates[setting_name] = values[argument]
    asr_settings = settings.asr.model_copy(update=updates)
    engine = FasterWhisperCliEngine(asr_settings, paths)
    with reserve_python_output(output, runtime_dir=paths.runtime_dir):
        result = engine.transcribe(source, language=values.get("language"))
        cues = [
            SubtitleCue(
                start_frame=max(0, round(segment.start_seconds * 1000)),
                end_frame=max(
                    round(segment.start_seconds * 1000) + 1,
                    round(segment.end_seconds * 1000),
                ),
                text=segment.text,
            )
            for segment in result.segments
        ]
        SubtitleFile.write_srt(
            output,
            cues,
            fps_numerator=1000,
            fps_denominator=1,
        )
    component_status = components.status(probe=True)["faster-whisper-xxl"]
    return SpeechTranscriptionResult(
        engine_version=str(component_status["version"]),
        input_path=str(source),
        input_sha256=_sha256(source),
        output_path=str(output),
        output_sha256=_sha256(output),
        language=result.language,
        duration_seconds=result.duration_seconds,
        segments=[
            SpeechSegmentResult(
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                text=segment.text,
            )
            for segment in result.segments
        ],
    )


def synthesize(context: OperationContext) -> SpeechSynthesisResult:
    settings, paths = _runtime(context)
    values = context.arguments
    output = Path(str(values["output_path"])).expanduser().resolve()
    components = RuntimeComponentService(settings, paths)
    installation = components.resolve("gpt-sovits-v2pro")
    if installation is None:
        raise FileNotFoundError("请先安装或选择 GPT-SoVITS v2Pro")
    engine = GptSoVitsEngine(
        installation.root,
        paths.runtime_dir,
        device=settings.speech_synthesis.device,
        startup_timeout_seconds=settings.speech_synthesis.startup_timeout_seconds,
    )
    with reserve_python_output(output, runtime_dir=paths.runtime_dir):
        result = engine.synthesize(
            text=str(values["text"]),
            text_language=str(values["text_language"]),
            reference_audio=str(values["reference_audio"]),
            reference_text=str(values["reference_text"]),
            reference_language=str(values["reference_language"]),
            output_path=output,
            auxiliary_reference_audio=[
                str(item) for item in values.get("auxiliary_reference_audio") or ()
            ],
            speed_factor=float(values.get("speed_factor", 1.0)),
            seed=int(values.get("seed", -1)),
            timeout_seconds=int(values.get("timeout_seconds", 900)),
            overwrite=bool(values.get("overwrite", False)),
        )
    return SpeechSynthesisResult(
        engine_version=installation.definition.version,
        output_path=str(result.output_path),
        output_sha256=result.sha256,
        duration_seconds=result.duration_seconds,
        sample_rate=result.sample_rate,
        channels=result.channels,
        reference_audio_sha256=result.reference_audio_sha256,
        device=result.device,
    )


def _runtime(context: OperationContext):
    if context.application is not None:
        return context.application.settings, context.application.runtime_paths
    return SettingsRepository().load(), RuntimePaths.discover()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
