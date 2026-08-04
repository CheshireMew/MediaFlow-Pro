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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from mediaflow.domain.asr import AsrEngine, AsrProgress, AsrResult, AsrSegment, AsrWord
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.subtitle_file import SubtitleFile

from .asr_models import FasterWhisperModelStore
from .audio_chunking import AudioChunkingService, AudioPreparationService
from .cache_manager import CacheManager
from .runtime_paths import RuntimePaths
from .system_resources import available_physical_memory_bytes


class AsrPipeline:
    """Prepare one requested source region and transcribe only that region."""

    def __init__(
        self,
        engine: AsrEngine,
        paths: RuntimePaths,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ):
        self.engine = engine
        self.paths = paths
        self.check_cancelled = check_cancelled

    def transcribe_region(
        self,
        media_path: str | Path,
        *,
        start_seconds: float,
        end_seconds: float,
        language: str | None = None,
        progress: AsrProgress | None = None,
    ) -> AsrResult:
        prepared = AudioPreparationService(self.paths).prepare_for_asr(
            media_path,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            check_cancelled=self.check_cancelled,
            progress=progress,
        )
        cache = CacheManager(self.paths.runtime_dir / "cache")
        try:
            return self.engine.transcribe(
                prepared,
                language=language,
                progress=progress,
            )
        finally:
            cache.cleanup_run(prepared.parent)


class ChunkedAsrEngine:
    """Apply one silence-aware, resource-bounded chunk strategy to every backend."""

    def __init__(
        self,
        engine: AsrEngine,
        settings: AsrSettings,
        paths: RuntimePaths,
        *,
        check_cancelled: Callable[[], None] | None = None,
        threshold_seconds: float = 900.0,
        target_chunk_seconds: float = 600.0,
        worker_count: int | None = None,
    ):
        self.engine = engine
        self.settings = settings
        self.paths = paths
        self.check_cancelled = check_cancelled
        self.threshold_seconds = threshold_seconds
        self.target_chunk_seconds = target_chunk_seconds
        self.worker_count = worker_count

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
        silences = chunking.detect_silence(
            media_path,
            duration_seconds=duration,
            check_cancelled=self.check_cancelled,
            progress=progress,
        )
        chunks = chunking.extract_chunks(
            media_path,
            chunking.split_points(
                duration,
                silences,
                target_duration=self.target_chunk_seconds,
            ),
            total_duration=duration,
            check_cancelled=self.check_cancelled,
            progress=progress,
        )
        cache = CacheManager(self.paths.runtime_dir / "cache")
        try:
            return self._transcribe_chunks(
                chunks,
                duration,
                language=language,
                progress=progress,
            )
        finally:
            cache.cleanup_run(chunks[0][0].parent)

    def _transcribe_chunks(
        self,
        chunks: list[tuple[Path, float]],
        duration: float,
        *,
        language: str | None,
        progress: AsrProgress | None,
    ) -> AsrResult:
        chunk_durations = [
            (chunks[index + 1][1] if index + 1 < len(chunks) else duration) - offset
            for index, (_path, offset) in enumerate(chunks)
        ]
        completed_seconds = [0.0] * len(chunks)
        results: list[AsrResult | None] = [None] * len(chunks)
        progress_lock = threading.Lock()
        workers = min(
            len(chunks),
            self.worker_count
            if self.worker_count is not None
            else recommended_chunk_workers(self.settings, self.paths),
        )

        def transcribe_chunk(index: int) -> tuple[int, AsrResult]:
            if self.check_cancelled:
                self.check_cancelled()

            def report_chunk(value: OperationProgress) -> None:
                if not progress:
                    return
                with progress_lock:
                    if (
                        value.mode == "determinate"
                        and value.completed is not None
                        and value.total is not None
                    ):
                        completed_seconds[index] = max(
                            completed_seconds[index],
                            chunk_durations[index] * (
                                value.completed / value.total
                            ),
                        )
                        progress(
                            OperationProgress.determinate(
                                (
                                    "asr_chunks_transcribing"
                                    if value.message_code == "transcribing"
                                    else value.message_code
                                ),
                                completed=sum(completed_seconds),
                                total=duration,
                                unit="media_seconds",
                            )
                        )
                    else:
                        progress(OperationProgress.indeterminate(value.message_code))

            result = self.engine.transcribe(
                chunks[index][0],
                language=language,
                progress=report_chunk,
            )
            with progress_lock:
                completed_seconds[index] = chunk_durations[index]
                if progress:
                    progress(
                        OperationProgress.determinate(
                            "asr_chunks_transcribing",
                            completed=sum(completed_seconds),
                            total=duration,
                            unit="media_seconds",
                        )
                    )
            return index, result

        with ThreadPoolExecutor(
            max_workers=max(1, workers),
            thread_name_prefix="mediaflow-asr-chunk",
        ) as executor:
            futures = [
                executor.submit(transcribe_chunk, index)
                for index in range(len(chunks))
            ]
            for future in as_completed(futures):
                index, result = future.result()
                results[index] = result

        output: list[AsrSegment] = []
        detected_language = language or "unknown"
        for index, (_path, offset) in enumerate(chunks):
            chunk_result = results[index]
            if chunk_result is None:
                raise RuntimeError(f"转录分块没有返回结果：{index + 1}")
            detected_language = chunk_result.language or detected_language
            output.extend(
                AsrSegment(
                    start_seconds=segment.start_seconds + offset,
                    end_seconds=segment.end_seconds + offset,
                    text=segment.text,
                    confidence=segment.confidence,
                    words=tuple(
                        AsrWord(
                            start_seconds=word.start_seconds + offset,
                            end_seconds=word.end_seconds + offset,
                            text=word.text,
                            confidence=word.confidence,
                        )
                        for word in segment.words
                    ),
                )
                    for segment in chunk_result.segments
            )
        return AsrResult(
            language=detected_language,
            duration_seconds=duration,
            segments=tuple(sorted(output, key=lambda item: (item.start_seconds, item.end_seconds))),
        )


class FasterWhisperEngine:
    def __init__(
        self,
        settings: AsrSettings,
        paths: RuntimePaths,
    ):
        self.settings = settings
        self.paths = paths
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
            word_timestamps=True,
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
                    words=tuple(
                        AsrWord(
                            start_seconds=float(word.start),
                            end_seconds=float(word.end),
                            text=str(word.word),
                            confidence=(
                                float(word.probability)
                                if getattr(word, "probability", None) is not None
                                else None
                            ),
                        )
                        for word in (getattr(segment, "words", None) or ())
                        if str(getattr(word, "word", "")).strip()
                    ),
                )
            )
            if progress and duration > 0:
                progress(
                    OperationProgress.determinate(
                        "transcribing",
                        completed=min(duration, float(segment.end)),
                        total=duration,
                        unit="media_seconds",
                    )
                )
        return AsrResult(
            language=str(getattr(info, "language", None) or requested_language or "unknown"),
            duration_seconds=duration,
            segments=tuple(result),
        )

    def _load_model(self, progress: AsrProgress | None):
        if self._model is not None:
            return self._model
        if progress:
            progress(OperationProgress.indeterminate("loading_asr_model"))
        import ctranslate2
        from faster_whisper import WhisperModel

        device = self.settings.device
        if self.paths.target.operating_system == "macos":
            device = "cpu"
        elif device == "auto":
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute_type = self.settings.compute_type
        if device == "cpu" and compute_type in {"float16", "int8_float16"}:
            compute_type = "int8"
        model_store = FasterWhisperModelStore(self.settings, self.paths)
        model_root = model_store.prepare()
        self._model = WhisperModel(
            model_store.builtin_model_reference(),
            device=device,
            compute_type=compute_type,
            download_root=str(model_root),
        )
        return self._model


def _whisper_process_entry(
    messages,
    settings_data: dict,
    paths: RuntimePaths,
    media_path: str,
    language: str | None,
) -> None:
    try:
        settings = AsrSettings.model_validate(settings_data)

        def report(value: OperationProgress) -> None:
            messages.put(
                (
                    "progress",
                    value.model_dump(mode="json", exclude_computed_fields=True),
                )
            )

        result = FasterWhisperEngine(settings, paths).transcribe(
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
        paths: RuntimePaths,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ):
        self.settings = settings
        self.paths = paths
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
                progress(OperationProgress.indeterminate("asr_cuda_cpu_fallback"))
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
                self.paths,
                str(source),
                language,
            ),
            name=f"{PRODUCT_NAME} ASR",
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
                        progress(OperationProgress.model_validate(message[1]))
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
                                words=tuple(
                                    AsrWord(
                                        start_seconds=float(word["start_seconds"]),
                                        end_seconds=float(word["end_seconds"]),
                                        text=str(word["text"]),
                                        confidence=(
                                            float(word["confidence"])
                                            if word["confidence"] is not None
                                            else None
                                        ),
                                    )
                                    for word in item.get("words", [])
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
        paths: RuntimePaths,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ):
        self.settings = settings
        self.paths = paths
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
                progress(OperationProgress.indeterminate("asr_cuda_cpu_fallback"))
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
        cache = CacheManager(self.paths.runtime_dir / "cache")
        output_dir = cache.create_run("asr-cli")
        try:
            command = self.build_command(source, output_dir, language=language)
            if progress:
                progress(OperationProgress.indeterminate("asr_cli_starting"))
            returncode, output = self._run(command, progress)
            srt_path = next(
                (
                    path
                    for path in sorted(output_dir.rglob("*.srt"))
                    if path.is_file() and path.stat().st_size > 0
                ),
                None,
            )
            if returncode != 0 and not (
                srt_path is not None and returncode in self.WINDOWS_OUTPUT_EXIT_CODES
            ):
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
            return AsrResult(
                language=(
                    requested_language
                    if requested_language and requested_language != "auto"
                    else "und"
                ),
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
        finally:
            cache.cleanup_run(output_dir)

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
                str(FasterWhisperModelStore(self.settings, self.paths).prepare()),
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
        if self.paths.target.key != "windows-x86_64":
            raise RuntimeError(
                "Faster-Whisper XXL is not available for "
                f"{self.paths.target.key}; use the built-in faster-whisper engine"
            )
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
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
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
                    progress(
                        OperationProgress.determinate(
                            "transcribing",
                            completed=min(100, int(match.group(1))),
                            total=100,
                            unit="percent",
                        )
                    )
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


def create_asr_pipeline(
    settings: AsrSettings,
    paths: RuntimePaths,
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> AsrPipeline:
    runtime_paths = paths
    if settings.engine == "faster_whisper_cli":
        backend: AsrEngine = FasterWhisperCliEngine(
            settings,
            runtime_paths,
            check_cancelled=check_cancelled,
        )
    else:
        backend = FasterWhisperProcessEngine(
            settings,
            runtime_paths,
            check_cancelled=check_cancelled,
        )
    return AsrPipeline(
        ChunkedAsrEngine(
            backend,
            settings,
            runtime_paths,
            check_cancelled=check_cancelled,
        ),
        runtime_paths,
        check_cancelled=check_cancelled,
    )


def recommended_chunk_workers(
    settings: AsrSettings,
    paths: RuntimePaths,
) -> int:
    if settings.parallel_chunks > 0:
        return settings.parallel_chunks
    cpu_count = max(1, os.cpu_count() or 1)
    available_memory = available_physical_memory_bytes()
    model_memory = _estimated_model_memory_bytes(settings.model)
    memory_workers = max(1, int(available_memory * 0.60 // model_memory))
    if _resolved_device(settings, paths) == "cuda":
        free_vram = _cuda_free_memory_bytes()
        if free_vram is not None:
            memory_workers = max(1, int(free_vram * 0.80 // model_memory))
        return max(1, min(2, memory_workers))
    return max(1, min(2, cpu_count // 4, memory_workers))


def _resolved_device(settings: AsrSettings, paths: RuntimePaths) -> str:
    if paths.target.operating_system == "macos":
        return "cpu"
    if settings.device != "auto":
        return settings.device
    return "cuda" if shutil.which("nvidia-smi") else "cpu"


def _estimated_model_memory_bytes(model: str) -> int:
    name = Path(model).name.lower()
    gib = 1.0
    if "large" in name:
        gib = 5.0 if "turbo" in name else 7.0
    elif "medium" in name:
        gib = 4.0
    elif "small" in name:
        gib = 2.5
    elif "base" in name:
        gib = 1.5
    return int(gib * 1024**3)


def _cuda_free_memory_bytes() -> int | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    result = subprocess.run(
        [
            executable,
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0
        ),
    )
    if result.returncode != 0:
        return None
    values = [
        int(match.group(0))
        for line in result.stdout.splitlines()
        if (match := re.search(r"\d+", line))
    ]
    return max(values) * 1024**2 if values else None


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
