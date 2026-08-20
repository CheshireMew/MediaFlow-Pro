from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.speech import (
    SpeechSegmentResult,
    SpeechSynthesisResult,
    SpeechSynthesizeArguments,
    SpeechTranscribeArguments,
    SpeechTranscriptionResult,
)
from mediaflow.domain.subtitle_file import SubtitleCue
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.asr_engine import FasterWhisperCliEngine
from mediaflow.infrastructure.gpt_sovits_engine import GptSoVitsEngine
from mediaflow.infrastructure.output_reservation import reserve_python_output
from mediaflow.infrastructure.runtime_components import RuntimeComponentService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subtitle_file_store import LocalSubtitleFileStore


class InfrastructureSpeechService:
    def __init__(
        self,
        settings: Callable[[], ServiceSettings],
        paths: RuntimePaths,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._subtitle_files = LocalSubtitleFileStore()

    def transcribe(
        self,
        request: SpeechTranscribeArguments,
    ) -> SpeechTranscriptionResult:
        settings = self._settings()
        source = Path(request.input_path).expanduser().resolve(strict=True)
        output = Path(request.output_path).expanduser().resolve()
        if output.suffix.lower() != ".srt":
            raise ValueError("speech.transcribe 的 output_path 必须是 .srt")
        if output.exists() and not request.overwrite:
            raise FileExistsError(f"输出已存在：{output}")
        components = RuntimeComponentService(settings, self._paths)
        installation = components.resolve("faster-whisper-xxl")
        if installation is None:
            raise FileNotFoundError("请先安装或选择 Faster-Whisper XXL")
        updates: dict[str, object] = {"cli_path": str(installation.entrypoint)}
        for value, setting_name in (
            (request.model, "model"),
            (request.device, "device"),
            (request.compute_type, "compute_type"),
        ):
            if value is not None:
                updates[setting_name] = value
        engine = FasterWhisperCliEngine(
            settings.asr.model_copy(update=updates),
            self._paths,
        )
        with reserve_python_output(output, runtime_dir=self._paths.runtime_dir):
            result = engine.transcribe(source, language=request.language)
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
            self._subtitle_files.write_srt(
                output,
                cues,
                fps_numerator=1000,
                fps_denominator=1,
            )
        component_status = components.status(probe=True)["faster-whisper-xxl"]
        return SpeechTranscriptionResult(
            engine_version=str(component_status["version"]),
            input_path=str(source),
            input_sha256=sha256_file(source),
            output_path=str(output),
            output_sha256=sha256_file(output),
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

    def synthesize(self, request: SpeechSynthesizeArguments) -> SpeechSynthesisResult:
        settings = self._settings()
        output = Path(request.output_path).expanduser().resolve()
        components = RuntimeComponentService(settings, self._paths)
        installation = components.resolve("gpt-sovits-v2pro")
        if installation is None:
            raise FileNotFoundError("请先安装或选择 GPT-SoVITS v2Pro")
        engine = GptSoVitsEngine(
            installation.root,
            self._paths.runtime_dir,
            device=settings.speech_synthesis.device,
            startup_timeout_seconds=settings.speech_synthesis.startup_timeout_seconds,
        )
        with reserve_python_output(output, runtime_dir=self._paths.runtime_dir):
            result = engine.synthesize(
                text=request.text,
                text_language=request.text_language,
                reference_audio=request.reference_audio,
                reference_text=request.reference_text,
                reference_language=request.reference_language,
                output_path=output,
                auxiliary_reference_audio=list(request.auxiliary_reference_audio),
                speed_factor=request.speed_factor,
                seed=request.seed,
                timeout_seconds=request.timeout_seconds,
                overwrite=request.overwrite,
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
