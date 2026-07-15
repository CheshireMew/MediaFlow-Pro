from __future__ import annotations

import multiprocessing
import os
import queue
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from mediaflow.domain.settings import AsrSettings

from .runtime_paths import RuntimePaths

AsrProgress = Callable[[float, str], None]


@dataclass(frozen=True, slots=True)
class AsrSegment:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class AsrResult:
    language: str
    duration_seconds: float
    segments: tuple[AsrSegment, ...]


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
        requested_language = language or self.settings.language
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
