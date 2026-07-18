from __future__ import annotations

import multiprocessing
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from mediaflow.domain.asr import AsrEngine, AsrProgress, AsrResult, AsrSegment
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.subtitle_file import SubtitleFile

from .audio_chunking import AudioChunkingService, AudioPreparationService
from .runtime_paths import RuntimePaths


class PreparedAudioAsrEngine:
    """Publish one phase-safe, 16 kHz mono PCM input to every ASR engine."""

    def __init__(
        self,
        engine: AsrEngine,
        paths: RuntimePaths | None = None,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ):
        self.engine = engine
        self.paths = paths or RuntimePaths.discover()
        self.check_cancelled = check_cancelled

    def transcribe(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult:
        if progress:
            progress(1.0, "preparing_asr_audio")
        prepared = AudioPreparationService(self.paths).prepare_for_asr(
            media_path,
            check_cancelled=self.check_cancelled,
        )

        def report(value: float, code: str) -> None:
            if progress:
                progress(10.0 + min(100.0, max(0.0, value)) * 0.9, code)

        result = self.engine.transcribe(
            prepared,
            language=language,
            progress=report,
        )
        if progress:
            progress(100.0, "transcription_completed")
        return result


class LongAudioAsrEngine:
    """Apply the original silence-aware long-media strategy around one ASR engine."""

    def __init__(
        self,
        engine: AsrEngine,
        paths: RuntimePaths | None = None,
        *,
        check_cancelled: Callable[[], None] | None = None,
        threshold_seconds: float = 900.0,
        target_chunk_seconds: float = 600.0,
    ):
        self.engine = engine
        self.paths = paths or RuntimePaths.discover()
        self.check_cancelled = check_cancelled
        self.threshold_seconds = threshold_seconds
        self.target_chunk_seconds = target_chunk_seconds

    def transcribe(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult:
        chunking = AudioChunkingService(self.paths)
        duration = chunking.duration_seconds(media_path)
        if duration <= self.threshold_seconds:
            return self.engine.transcribe(
                media_path,
                language=language,
                progress=progress,
            )
        if progress:
            progress(5.0, "asr_audio_splitting")
        silences = chunking.detect_silence(
            media_path,
            check_cancelled=self.check_cancelled,
        )
        chunks = chunking.extract_chunks(
            media_path,
            chunking.split_points(
                duration,
                silences,
                target_duration=self.target_chunk_seconds,
            ),
            check_cancelled=self.check_cancelled,
        )
        output: list[AsrSegment] = []
        detected_language = language or "unknown"
        for index, (path, offset) in enumerate(chunks):
            if self.check_cancelled:
                self.check_cancelled()

            def report(
                value: float,
                _code: str,
                chunk_index: int = index,
                chunk_total: int = len(chunks),
            ) -> None:
                if progress:
                    progress(
                        20.0 + ((chunk_index + min(100.0, value) / 100.0) / chunk_total) * 75.0,
                        "asr_chunks_progress",
                    )

            result = self.engine.transcribe(path, language=language, progress=report)
            detected_language = result.language or detected_language
            output.extend(
                AsrSegment(
                    start_seconds=segment.start_seconds + offset,
                    end_seconds=segment.end_seconds + offset,
                    text=segment.text,
                    confidence=segment.confidence,
                )
                for segment in result.segments
            )
        if progress:
            progress(100.0, "transcription_completed")
        return AsrResult(
            language=detected_language,
            duration_seconds=duration,
            segments=tuple(sorted(output, key=lambda item: (item.start_seconds, item.end_seconds))),
        )


class FasterWhisperEngine:
    def __init__(
        self,
        settings: AsrSettings,
        paths: RuntimePaths | None = None,
    ):
        self.settings = settings
        self.paths = paths or RuntimePaths.discover()
        self._model = None

    def transcribe(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult:
        source = Path(media_path).resolve(strict=True)
        model = self._load_model(progress)
        requested_language: str | None = language or self.settings.language
        if requested_language == "auto":
            requested_language = None
        segments, info = model.transcribe(
            str(source),
            language=requested_language,
            beam_size=5,
            vad_filter=True,
            word_timestamps=False,
            condition_on_previous_text=True,
        )
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        result: list[AsrSegment] = []
        for segment in segments:
            text = str(segment.text).strip()
            if not text:
                continue
            confidence = None
            average_logprob = getattr(segment, "avg_logprob", None)
            if average_logprob is not None:
                confidence = max(0.0, min(1.0, 1.0 + float(average_logprob)))
            result.append(
                AsrSegment(
                    start_seconds=float(segment.start),
                    end_seconds=float(segment.end),
                    text=text,
                    confidence=confidence,
                )
            )
            if progress and duration > 0:
                progress(min(98.0, float(segment.end) / duration * 98.0), "transcribing")
        if progress:
            progress(100.0, "transcription_completed")
        return AsrResult(
            language=str(getattr(info, "language", None) or requested_language or "unknown"),
            duration_seconds=duration,
            segments=tuple(result),
        )

    def _load_model(self, progress: AsrProgress | None):
        if self._model is not None:
            return self._model
        if progress:
            progress(1.0, "loading_asr_model")
        import ctranslate2
        from faster_whisper import WhisperModel

        device = self.settings.device
        if device == "auto":
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute_type = self.settings.compute_type
        if device == "cpu" and compute_type in {"float16", "int8_float16"}:
            compute_type = "int8"
        model_root = self.paths.runtime_dir / "models" / "faster-whisper"
        model_root.mkdir(parents=True, exist_ok=True)
        self._model = WhisperModel(
            self.settings.model,
            device=device,
            compute_type=compute_type,
            download_root=str(model_root),
        )
        return self._model


def _whisper_process_entry(
    messages,
    settings_data: dict,
    runtime_dir: str,
    media_path: str,
    language: str | None,
) -> None:
    try:
        os.environ["MEDIAFLOW_RUNTIME_DIR"] = runtime_dir
        settings = AsrSettings.model_validate(settings_data)

        def report(value: float, code: str) -> None:
            messages.put(("progress", float(value), str(code)))

        result = FasterWhisperEngine(settings).transcribe(
            media_path,
            language=language,
            progress=report,
        )
        messages.put(("result", asdict(result)))
    except BaseException:
        messages.put(("error", traceback.format_exc()))


class FasterWhisperProcessEngine:
    """Run heavy ASR inference outside the GUI and task-scheduler process."""

    def __init__(
        self,
        settings: AsrSettings,
        paths: RuntimePaths | None = None,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ):
        self.settings = settings
        self.paths = paths or RuntimePaths.discover()
        self.check_cancelled = check_cancelled

    def transcribe(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult:
        try:
            return self._transcribe_once(
                media_path,
                language=language,
                progress=progress,
            )
        except RuntimeError as error:
            if self.settings.device not in {"auto", "cuda"} or not _is_cuda_error(error):
                raise
            if progress:
                progress(2.0, "asr_cuda_cpu_fallback")
            fallback = FasterWhisperProcessEngine(
                self.settings.model_copy(update={"device": "cpu", "compute_type": "int8"}),
                self.paths,
                check_cancelled=self.check_cancelled,
            )
            return fallback._transcribe_once(
                media_path,
                language=language,
                progress=progress,
            )

    def _transcribe_once(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult:
        source = Path(media_path).resolve(strict=True)
        context = multiprocessing.get_context("spawn")
        messages = context.Queue()
        process = context.Process(
            target=_whisper_process_entry,
            args=(
                messages,
                self.settings.model_dump(mode="json"),
                str(self.paths.runtime_dir),
                str(source),
                language,
            ),
            name="MediaFlow-ASR",
        )
        process.start()
        try:
            while True:
                try:
                    message = messages.get(timeout=0.25)
                except queue.Empty:
                    if self.check_cancelled:
                        self.check_cancelled()
                    if not process.is_alive():
                        raise RuntimeError(
                            f"ASR worker exited unexpectedly with code {process.exitcode}"
                        ) from None
                    continue
                kind = message[0]
                if kind == "progress":
                    if progress:
                        progress(float(message[1]), str(message[2]))
                    continue
                if kind == "error":
                    raise RuntimeError(f"ASR worker failed:\n{message[1]}")
                if kind == "result":
                    payload = message[1]
                    return AsrResult(
                        language=str(payload["language"]),
                        duration_seconds=float(payload["duration_seconds"]),
                        segments=tuple(
                            AsrSegment(
                                start_seconds=float(item["start_seconds"]),
                                end_seconds=float(item["end_seconds"]),
                                text=str(item["text"]),
                                confidence=(
                                    float(item["confidence"]) if item["confidence"] is not None else None
                                ),
                            )
                            for item in payload["segments"]
                        ),
                    )
        except BaseException:
            if process.is_alive():
                process.terminate()
            raise
        finally:
            process.join(timeout=5.0)
            if process.is_alive():
                process.kill()
                process.join()
            messages.close()
            messages.join_thread()


class FasterWhisperCliEngine:
    """Run the standalone Faster-Whisper XXL executable and consume its real SRT output."""

    WINDOWS_OUTPUT_EXIT_CODES = {
        3221226505,
        3221225477,
        -1073740791,
        -1073741819,
    }

    def __init__(
        self,
        settings: AsrSettings,
        paths: RuntimePaths | None = None,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ):
        self.settings = settings
        self.paths = paths or RuntimePaths.discover()
        self.check_cancelled = check_cancelled

    def transcribe(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult:
        try:
            return self._transcribe_once(media_path, language=language, progress=progress)
        except RuntimeError as error:
            if self.settings.device != "cuda" or not _is_cuda_error(error):
                raise
            if progress:
                progress(2.0, "asr_cuda_cpu_fallback")
            return FasterWhisperCliEngine(
                self.settings.model_copy(update={"device": "cpu"}),
                self.paths,
                check_cancelled=self.check_cancelled,
            )._transcribe_once(media_path, language=language, progress=progress)

    def _transcribe_once(
        self,
        media_path: str | Path,
        *,
        language: str | None,
        progress: AsrProgress | None,
    ) -> AsrResult:
        source = Path(media_path).resolve(strict=True)
        output_dir = self.paths.runtime_dir / "cache" / "asr-cli" / "runs" / str(uuid.uuid4())
        output_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command(source, output_dir, language=language)
        if progress:
            progress(1.0, "asr_cli_starting")
        returncode, output = self._run(command, progress)
        srt_path = next(
            (
                path
                for path in sorted(output_dir.rglob("*.srt"))
                if path.is_file() and path.stat().st_size > 0
            ),
            None,
        )
        if returncode != 0 and not (srt_path is not None and returncode in self.WINDOWS_OUTPUT_EXIT_CODES):
            detail = "\n".join(output[-30:]).strip() or "没有 CLI 输出"
            raise RuntimeError(f"Faster-Whisper CLI 失败（{returncode}）：{detail}")
        if srt_path is None:
            raise RuntimeError("Faster-Whisper CLI 没有生成可用的 SRT")
        cues = SubtitleFile.read(
            srt_path,
            fps_numerator=1000,
            fps_denominator=1,
        )
        requested_language = language or self.settings.language
        if progress:
            progress(100.0, "transcription_completed")
        return AsrResult(
            language=(requested_language if requested_language and requested_language != "auto" else "und"),
            duration_seconds=max(cue.end_frame for cue in cues) / 1000,
            segments=tuple(
                AsrSegment(
                    start_seconds=cue.start_frame / 1000,
                    end_seconds=cue.end_frame / 1000,
                    text=cue.text,
                )
                for cue in cues
            ),
        )

    def build_command(
        self,
        media_path: str | Path,
        output_dir: str | Path,
        *,
        language: str | None = None,
    ) -> list[str]:
        cli_path = self._cli_path()
        command = [sys.executable, str(cli_path)] if cli_path.suffix.lower() == ".py" else [str(cli_path)]
        device = self.settings.device
        if device == "auto":
            device = "cuda" if shutil.which("nvidia-smi") else "cpu"
        command.extend(
            [
                str(Path(media_path).resolve(strict=True)),
                "--model",
                Path(self.settings.model).name,
                "--model_dir",
                str(self.paths.runtime_dir / "models" / "faster-whisper"),
                "-o",
                str(Path(output_dir).resolve()),
                "--output_format",
                "srt",
                "--print_progress",
                "--vad_filter",
                "True",
                "--device",
                device,
                "--sentence",
                "--max_comma",
                "20",
                "--max_comma_cent",
                "50",
                "--initial_prompt",
                "None",
            ]
        )
        requested_language = language or self.settings.language
        if requested_language and requested_language != "auto":
            command.extend(["--language", requested_language])
        return command

    def _cli_path(self) -> Path:
        candidates = [
            Path(self.settings.cli_path).expanduser() if self.settings.cli_path else None,
            self.paths.runtime_dir / "tools" / "Faster-Whisper-XXL" / "faster-whisper-xxl.exe",
        ]
        path = next((item for item in candidates if item and item.is_file()), None)
        if path is None:
            raise FileNotFoundError("请先安装或选择 Faster-Whisper XXL 可执行文件")
        return path.resolve()

    def _run(
        self,
        command: list[str],
        progress: AsrProgress | None,
    ) -> tuple[int, list[str]]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line.rstrip())
            lines.put(None)

        reader = threading.Thread(target=read_output, name="mediaflow-asr-cli-output", daemon=True)
        reader.start()
        captured: list[str] = []
        try:
            while True:
                try:
                    line = lines.get(timeout=0.25)
                except queue.Empty:
                    if self.check_cancelled:
                        self.check_cancelled()
                    continue
                if line is None:
                    break
                captured.append(line)
                match = re.search(r"(?<![\d.])(\d{1,3})%", line)
                if match and progress and "MB" not in line and "kB" not in line:
                    progress(5 + min(100, int(match.group(1))) * 0.9, "transcribing")
                if self.check_cancelled:
                    self.check_cancelled()
            return process.wait(timeout=30), captured
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
        finally:
            reader.join(timeout=2)


def create_asr_engine(
    settings: AsrSettings,
    paths: RuntimePaths | None = None,
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> AsrEngine:
    runtime_paths = paths or RuntimePaths.discover()
    if settings.engine == "faster_whisper_cli":
        engine: AsrEngine = FasterWhisperCliEngine(
            settings,
            runtime_paths,
            check_cancelled=check_cancelled,
        )
    else:
        engine = LongAudioAsrEngine(
            FasterWhisperProcessEngine(
                settings,
                runtime_paths,
                check_cancelled=check_cancelled,
            ),
            runtime_paths,
            check_cancelled=check_cancelled,
        )
    return PreparedAudioAsrEngine(
        engine,
        runtime_paths,
        check_cancelled=check_cancelled,
    )


def _is_cuda_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "cuda failed",
            "cuda driver",
            "no cuda",
            "cublas",
            "cudnn",
            "cudart",
            "cannot be loaded",
        )
    )
