from __future__ import annotations

import json
import logging
import math
import multiprocessing
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from mediaflow.domain.asr import AsrEngine, AsrProgress, AsrResult, AsrSegment, AsrWord
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import AsrSettings, ServiceSettings
from mediaflow.infrastructure.subtitle_file_store import LocalSubtitleFileStore

from .asr_models import FasterWhisperModelStore
from .asr_resources import asr_inference_slot
from .audio_chunking import AudioChunkingService, AudioPreparationService
from .cache_manager import CacheManager
from .runtime_components import RuntimeComponentService
from .runtime_paths import RuntimePaths

logger = logging.getLogger(__name__)
LONG_AUDIO_SECONDS = 900.0


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


class FasterWhisperEngine:
    def __init__(
        self,
        settings: AsrSettings,
        paths: RuntimePaths,
        *,
        batch_size: int = 0,
    ):
        self.settings = settings
        self.paths = paths
        self.batch_size = batch_size
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
        transcriber = model
        batch_options = {}
        if self.batch_size:
            from faster_whisper import BatchedInferencePipeline

            transcriber = BatchedInferencePipeline(model)
            batch_options = {"batch_size": self.batch_size, "chunk_length": 30}
        segments, info = transcriber.transcribe(
            str(source),
            language=requested_language,
            beam_size=5,
            vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=not bool(self.batch_size),
            **batch_options,
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
        compute_type = self.settings.compute_type_for_device(device)
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
    batch_size: int,
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

        result = FasterWhisperEngine(settings, paths, batch_size=batch_size).transcribe(
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
        return _transcribe_with_retries(
            self.settings,
            self.paths,
            media_path,
            language=language,
            progress=progress,
            check_cancelled=self.check_cancelled,
            attempt=lambda settings, batch_size, report: FasterWhisperProcessEngine(
                settings,
                self.paths,
                check_cancelled=self.check_cancelled,
            )._transcribe_once(
                media_path,
                language=language,
                progress=report,
                batch_size=batch_size,
            ),
        )

    def _transcribe_once(
        self,
        media_path: str | Path,
        *,
        language: str | None = None,
        progress: AsrProgress | None = None,
        batch_size: int = 0,
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
                batch_size,
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
                if self.check_cancelled:
                    self.check_cancelled()
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
    """Run Faster-Whisper XXL and consume JSON word timing, or legacy SRT output."""

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
        return _transcribe_with_retries(
            self.settings,
            self.paths,
            media_path,
            language=language,
            progress=progress,
            check_cancelled=self.check_cancelled,
            attempt=lambda settings, batch_size, report: FasterWhisperCliEngine(
                settings,
                self.paths,
                check_cancelled=self.check_cancelled,
            )._transcribe_once(
                media_path,
                language=language,
                progress=report,
                batch_size=batch_size,
            ),
        )

    def _transcribe_once(
        self,
        media_path: str | Path,
        *,
        language: str | None,
        progress: AsrProgress | None,
        batch_size: int = 0,
    ) -> AsrResult:
        source = Path(media_path).resolve(strict=True)
        cache = CacheManager(self.paths.runtime_dir / "cache")
        output_dir = cache.create_run("asr-cli")
        try:
            command = self.build_command(
                source,
                output_dir,
                language=language,
                batch_size=batch_size,
            )
            if progress:
                progress(OperationProgress.indeterminate("asr_cli_starting"))
            returncode, output = self._run(command, progress)
            detail = "\n".join(output[-30:]).strip() or "没有 CLI 输出"
            if returncode != 0 and (
                returncode not in self.WINDOWS_OUTPUT_EXIT_CODES
                or _is_out_of_memory(RuntimeError(detail))
                or _is_cuda_error(RuntimeError(detail))
            ):
                raise RuntimeError(f"Faster-Whisper CLI 失败（{returncode}）：{detail}")
            result = self._read_result(output_dir, source, language)
            if result is None:
                # Some XXL versions omit output files when VAD finds no speech.
                if returncode == 0 and any(
                    marker in "\n".join(output).lower()
                    for marker in ("no speech", "no voice", "no active speech")
                ):
                    return AsrResult(language=language or "und", duration_seconds=0, segments=())
                raise RuntimeError(f"Faster-Whisper CLI 没有生成识别结果：{detail}")
            if returncode != 0 and not result.segments:
                raise RuntimeError(f"Faster-Whisper CLI 失败（{returncode}）：{detail}")
            return result
        finally:
            cache.cleanup_run(output_dir)

    def _read_result(
        self,
        output_dir: Path,
        source: Path,
        language: str | None,
    ) -> AsrResult | None:
        requested = language or self.settings.language
        detected = requested if requested and requested != "auto" else "und"
        json_paths = sorted(
            path for path in output_dir.rglob("*.json") if path.name != CacheManager.RUN_MANIFEST
        )
        if json_paths:
            path = next((p for p in json_paths if p.stem == source.stem), json_paths[0])
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
                raise RuntimeError("Faster-Whisper CLI JSON 缺少 segments")
            segments = tuple(
                AsrSegment(
                    start_seconds=float(item["start"]),
                    end_seconds=float(item["end"]),
                    text=str(item["text"]).strip(),
                    words=tuple(
                        AsrWord(
                            start_seconds=float(word["start"]),
                            end_seconds=float(word["end"]),
                            text=str(word["word"]),
                            confidence=word.get("probability"),
                        )
                        for word in item.get("words", [])
                        if str(word.get("word", "")).strip()
                    ),
                )
                for item in payload["segments"]
                if str(item.get("text", "")).strip()
            )
            return AsrResult(
                language=str(payload.get("language") or detected),
                duration_seconds=max((item.end_seconds for item in segments), default=0),
                segments=segments,
            )
        srt_paths = sorted(output_dir.rglob("*.srt"))
        if not srt_paths:
            return None
        path = next((p for p in srt_paths if p.stem == source.stem), srt_paths[0])
        if not path.read_text(encoding="utf-8-sig").strip():
            return AsrResult(language=detected, duration_seconds=0, segments=())
        cues = LocalSubtitleFileStore().read(path, fps_numerator=1000, fps_denominator=1)
        return AsrResult(
            language=detected,
            duration_seconds=max((cue.end_frame for cue in cues), default=0) / 1000,
            segments=tuple(
                AsrSegment(cue.start_frame / 1000, cue.end_frame / 1000, cue.text) for cue in cues
            ),
        )

    def build_command(
        self,
        media_path: str | Path,
        output_dir: str | Path,
        *,
        language: str | None = None,
        batch_size: int = 0,
    ) -> list[str]:
        cli_path = self._cli_path()
        command = [sys.executable, str(cli_path)] if cli_path.suffix.lower() == ".py" else [str(cli_path)]
        device = _resolved_device(self.settings, self.paths)
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
                "json",
                "srt",
                "--print_progress",
                "--vad_filter",
                "True",
                "--device",
                device,
                "--compute_type",
                self.settings.compute_type_for_device(device),
                "--word_timestamps",
                "True",
                "--initial_prompt",
                "None",
            ]
        )
        if batch_size:
            command.extend(["--batched", "--batch_size", str(batch_size), "--chunk_length", "30"])
        requested_language = language or self.settings.language
        if requested_language and requested_language != "auto":
            command.extend(["--language", requested_language])
        return command

    def _cli_path(self) -> Path:
        components = RuntimeComponentService(
            ServiceSettings(asr=self.settings),
            self.paths,
        )
        definition = components.catalog["faster-whisper-xxl"]
        if self.paths.target.key not in definition.targets:
            raise RuntimeError(
                "Faster-Whisper XXL is not available for "
                f"{self.paths.target.key}; use the built-in faster-whisper engine"
            )
        installation = components.resolve(definition.id)
        if installation is None:
            raise FileNotFoundError("请先安装或选择 Faster-Whisper XXL 可执行文件")
        return installation.entrypoint

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
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
        lines: queue.Queue[str | None] = queue.Queue(maxsize=256)
        stop_reader = threading.Event()

        def enqueue(line: str | None) -> None:
            while not stop_reader.is_set():
                try:
                    lines.put(line, timeout=0.1)
                    return
                except queue.Full:
                    continue

        def read_output() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    if stop_reader.is_set():
                        break
                    enqueue(line.rstrip())
            finally:
                enqueue(None)

        reader = threading.Thread(target=read_output, name="mediaflow-asr-cli-output", daemon=True)
        reader.start()
        captured: deque[str] = deque(maxlen=200)
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
            while process.poll() is None:
                if self.check_cancelled:
                    self.check_cancelled()
                threading.Event().wait(0.1)
            return process.returncode, list(captured)
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
            stop_reader.set()
            reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()


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
    return AsrPipeline(backend, runtime_paths, check_cancelled=check_cancelled)


def recommended_batch_size(settings: AsrSettings, paths: RuntimePaths) -> int:
    # The persisted key predates single-model batching; it now limits VAD segments
    # decoded together, never the number of model processes.
    return settings.parallel_chunks or (4 if _resolved_device(settings, paths) == "cuda" else 2)


def _resolved_device(settings: AsrSettings, paths: RuntimePaths) -> str:
    if paths.target.operating_system == "macos":
        return "cpu"
    if settings.device != "auto":
        return settings.device
    return "cuda" if shutil.which("nvidia-smi") else "cpu"


def _transcribe_with_retries(
    settings: AsrSettings,
    paths: RuntimePaths,
    media_path: str | Path,
    *,
    language: str | None,
    progress: AsrProgress | None,
    check_cancelled: Callable[[], None] | None,
    attempt: Callable[[AsrSettings, int, AsrProgress | None], AsrResult],
) -> AsrResult:
    if check_cancelled:
        check_cancelled()
    duration = AudioChunkingService(paths).duration_seconds(media_path)
    current = settings.model_copy(update={"device": _resolved_device(settings, paths)})
    current.compute_type = current.compute_type_for_device(current.device)
    batch_size = recommended_batch_size(current, paths) if duration > LONG_AUDIO_SECONDS else 0
    completed = 0.0

    def report(value: OperationProgress) -> None:
        nonlocal completed
        if check_cancelled:
            check_cancelled()
        if value.message_code == "transcribing" and value.percent is not None:
            completed = max(completed, value.percent)
            value = OperationProgress.determinate(
                "transcribing",
                completed=completed,
                total=100,
                unit="percent",
            )
        if progress:
            progress(value)

    with asr_inference_slot(paths, check_cancelled=check_cancelled, progress=progress):
        while True:
            if check_cancelled:
                check_cancelled()
            logger.info(
                "ASR engine=%s model=%s device=%s compute_type=%s batch_size=%d duration=%.3f",
                current.engine,
                current.model,
                current.device,
                current.compute_type,
                batch_size,
                duration,
            )
            try:
                result = attempt(current, batch_size, report)
                if check_cancelled:
                    check_cancelled()
                return normalize_asr_result(result, duration)
            except RuntimeError as error:
                if _is_out_of_memory(error):
                    if batch_size > 1:
                        batch_size = max(1, batch_size // 2)
                        logger.warning("ASR memory exhausted; retry batch_size=%d: %s", batch_size, error)
                        report(OperationProgress.indeterminate("asr_batch_reduced"))
                        continue
                    raise RuntimeError(
                        "转录内存或显存不足，批量已降至 1。请关闭占用资源的程序，"
                        "或选择更小的模型 / INT8 精度后重试；未自动切换设备或精度。"
                    ) from error
                if current.device != "cuda" or not _is_cuda_error(error):
                    raise
                logger.warning("ASR CUDA unavailable; retry on CPU: %s", error)
                current = current.model_copy(
                    update={
                        "device": "cpu",
                        "compute_type": current.compute_type_for_device("cpu"),
                    }
                )
                batch_size = min(batch_size, recommended_batch_size(current, paths))
                report(OperationProgress.indeterminate("asr_cuda_cpu_fallback"))


def normalize_asr_result(result: AsrResult, duration: float) -> AsrResult:
    """Clip at the source boundary without deleting genuine repeated speech."""
    segments = []
    for item in result.segments:
        if not math.isfinite(item.start_seconds) or not math.isfinite(item.end_seconds):
            continue
        start = max(0.0, item.start_seconds)
        end = min(duration, item.end_seconds)
        if end <= start or not item.text.strip():
            continue
        words = []
        for word in item.words:
            if not math.isfinite(word.start_seconds) or not math.isfinite(word.end_seconds):
                continue
            word_start, word_end = max(start, word.start_seconds), min(end, word.end_seconds)
            # Whisper can assign the same timestamp to a very short word or
            # punctuation. Keep its text; timeline projection gives it one frame.
            if word_end >= word_start and word.text.strip():
                words.append(replace(word, start_seconds=word_start, end_seconds=word_end))
        segments.append(replace(item, start_seconds=start, end_seconds=end, words=tuple(words)))
    return replace(
        result,
        duration_seconds=duration,
        segments=tuple(sorted(segments, key=lambda item: (item.start_seconds, item.end_seconds))),
    )


def _is_out_of_memory(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "out of memory",
            "out_of_memory",
            "cublas_status_alloc_failed",
            "cudnn_status_alloc_failed",
            "bad_alloc",
            "failed to allocate",
            "memoryerror",
        )
    )


def _is_cuda_error(error: Exception) -> bool:
    if _is_out_of_memory(error):
        return False
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
