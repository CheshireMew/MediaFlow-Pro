from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaflow.domain.asr import AsrResult, AsrSegment, AsrWord
from mediaflow.domain.settings import AsrSettings, ServiceSettings
from mediaflow.infrastructure import asr_engine as module
from mediaflow.infrastructure.asr_engine import (
    FasterWhisperCliEngine,
    FasterWhisperEngine,
    FasterWhisperProcessEngine,
    normalize_asr_result,
)
from mediaflow.infrastructure.asr_resources import asr_inference_slot
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_tools import RuntimeToolService


@pytest.fixture
def paths(tmp_path):
    return replace(RuntimeContext.discover().paths, runtime_dir=tmp_path / "runtime")


@pytest.mark.parametrize("engine_type", [FasterWhisperCliEngine, FasterWhisperProcessEngine])
@pytest.mark.parametrize(
    "message",
    [
        "CUDA out of memory",
        "CUDA failed with error out of memory",
        "CUBLAS_STATUS_ALLOC_FAILED",
    ],
)
def test_oom_reduces_batch_without_changing_model_device_or_precision(
    paths, monkeypatch, engine_type, message
):
    monkeypatch.setattr(module.AudioChunkingService, "duration_seconds", lambda *_: 960)
    attempts = []

    def once(self, source, *, language, progress, batch_size):
        attempts.append((self.settings.model, self.settings.device, self.settings.compute_type, batch_size))
        if batch_size > 1:
            raise RuntimeError(message)
        return AsrResult("zh", 960, (AsrSegment(0, 1, "原文"),))

    monkeypatch.setattr(engine_type, "_transcribe_once", once)
    result = engine_type(
        AsrSettings(device="cuda", model="large-v2", compute_type="float16"), paths
    ).transcribe("audio")
    assert result.segments[0].text == "原文"
    assert attempts == [("large-v2", "cuda", "float16", size) for size in (4, 2, 1)]


@pytest.mark.parametrize("engine_type", [FasterWhisperCliEngine, FasterWhisperProcessEngine])
def test_oom_at_one_reports_failure_without_cpu_fallback(paths, monkeypatch, engine_type):
    monkeypatch.setattr(module.AudioChunkingService, "duration_seconds", lambda *_: 960)
    attempts = []

    def once(self, *args, **kwargs):
        attempts.append((self.settings.device, kwargs["batch_size"]))
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(engine_type, "_transcribe_once", once)
    with pytest.raises(RuntimeError, match="未自动切换设备或精度"):
        engine_type(AsrSettings(device="cuda"), paths).transcribe("audio")
    assert attempts == [("cuda", 4), ("cuda", 2), ("cuda", 1)]


@pytest.mark.parametrize("engine_type", [FasterWhisperCliEngine, FasterWhisperProcessEngine])
def test_auto_cuda_environment_failure_retries_cpu_once(paths, monkeypatch, engine_type):
    monkeypatch.setattr(module.AudioChunkingService, "duration_seconds", lambda *_: 960)
    monkeypatch.setattr(module.shutil, "which", lambda _: "nvidia-smi")
    attempts = []

    def once(self, *args, **kwargs):
        attempts.append((self.settings.device, self.settings.compute_type, kwargs["batch_size"]))
        if self.settings.device == "cuda":
            raise RuntimeError("CUDA driver version is insufficient")
        return AsrResult("zh", 960, ())

    monkeypatch.setattr(engine_type, "_transcribe_once", once)
    engine_type(AsrSettings(device="auto"), paths).transcribe("audio")
    assert attempts == [("cuda", "float16", 4), ("cpu", "int8", 2)]


def test_builtin_batches_vad_chunks_on_one_loaded_model(paths, tmp_path, monkeypatch):
    import faster_whisper

    source = tmp_path / "input.wav"
    source.write_bytes(b"test")
    model = object()
    observed = []

    class Batch:
        def __init__(self, supplied):
            assert supplied is model

        def transcribe(self, source, **kwargs):
            observed.append(kwargs)
            return iter(()), SimpleNamespace(duration=960, language="zh")

    monkeypatch.setattr(faster_whisper, "BatchedInferencePipeline", Batch)
    engine = FasterWhisperEngine(AsrSettings(), paths, batch_size=4)
    monkeypatch.setattr(engine, "_load_model", lambda _: model)
    engine.transcribe(source)
    assert len(observed) == 1
    assert observed[0]["batch_size"] == 4
    assert observed[0]["chunk_length"] == 30
    assert observed[0]["vad_filter"] and observed[0]["word_timestamps"]


@pytest.mark.parametrize(
    "device, precision, expected",
    [
        ("cuda", "float16", "float16"),
        ("cuda", "int8_float16", "int8_float16"),
        ("cpu", "float16", "int8"),
        ("cpu", "float32", "float32"),
    ],
)
def test_cli_explicit_precision_and_batch_flags(paths, tmp_path, monkeypatch, device, precision, expected):
    source = tmp_path / "input.wav"
    source.write_bytes(b"test")
    monkeypatch.setattr(FasterWhisperCliEngine, "_cli_path", lambda _: Path(sys.executable))
    monkeypatch.setattr(module.FasterWhisperModelStore, "prepare", lambda _: tmp_path)
    command = FasterWhisperCliEngine(AsrSettings(device=device, compute_type=precision), paths).build_command(
        source, tmp_path, batch_size=4
    )
    assert command[command.index("--compute_type") + 1] == expected
    assert command[command.index("--batch_size") + 1] == "4"
    assert command[command.index("--output_format") + 1 : command.index("--output_format") + 3] == [
        "json",
        "srt",
    ]
    assert "--sentence" not in command


def test_json_words_win_over_merged_srt(paths, tmp_path):
    (tmp_path / "input.json").write_text(
        json.dumps(
            {
                "language": "zh",
                "segments": [
                    {
                        "start": 1,
                        "end": 4,
                        "text": "甲乙",
                        "words": [
                            {"start": 1, "end": 2, "word": "甲", "probability": 0.9},
                            {"start": 3, "end": 4, "word": "乙", "probability": 0.8},
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "input.srt").write_text("1\n00:00:01,000 --> 00:00:04,000\n错误长段\n", encoding="utf-8")
    result = FasterWhisperCliEngine(AsrSettings(), paths)._read_result(tmp_path, Path("input.wav"), "auto")
    assert result.language == "zh"
    assert result.segments[0].text == "甲乙"
    assert [(word.text, word.start_seconds, word.end_seconds) for word in result.segments[0].words] == [
        ("甲", 1, 2),
        ("乙", 3, 4),
    ]


@pytest.mark.parametrize("output", ["json", "srt", "none", "missing", "invalid"])
def test_empty_cli_result_is_distinct_from_missing_or_invalid_output(paths, tmp_path, monkeypatch, output):
    source = tmp_path / "input.wav"
    source.write_bytes(b"test")

    def run(self, command, progress):
        directory = Path(command[0])
        if output == "json":
            (directory / "input.json").write_text('{"segments": []}', encoding="utf-8")
        elif output == "invalid":
            (directory / "input.json").write_text("{}", encoding="utf-8")
        elif output == "srt":
            (directory / "input.srt").write_text("", encoding="utf-8")
        return 0, ["No speech found"] if output == "none" else []

    monkeypatch.setattr(
        FasterWhisperCliEngine, "build_command", lambda self, source, directory, **kw: [str(directory)]
    )
    monkeypatch.setattr(FasterWhisperCliEngine, "_run", run)
    engine = FasterWhisperCliEngine(AsrSettings(), paths)
    if output in {"missing", "invalid"}:
        with pytest.raises(RuntimeError):
            engine._transcribe_once(source, language="zh", progress=None)
    else:
        assert engine._transcribe_once(source, language="zh", progress=None).segments == ()


def test_normalize_bounds_and_keep_repeated_speech():
    result = normalize_asr_result(
        AsrResult(
            "zh",
            99,
            (
                AsrSegment(3, 5, "重复", words=(AsrWord(3, 5, "重复"),)),
                AsrSegment(-1, 1, "重复", words=(AsrWord(-1, 1, "重复"),)),
                AsrSegment(5, 6, "越界"),
            ),
        ),
        4,
    )
    assert [(item.start_seconds, item.end_seconds, item.text) for item in result.segments] == [
        (0, 1, "重复"),
        (3, 4, "重复"),
    ]
    assert result.segments[-1].words[-1].end_seconds == 4
    assert result.duration_seconds == 4


def test_zero_duration_words_and_punctuation_are_not_dropped():
    result = normalize_asr_result(
        AsrResult(
            "zh",
            1,
            (
                AsrSegment(
                    0,
                    1,
                    "甲乙。",
                    words=(
                        AsrWord(0, 0.5, "甲"),
                        AsrWord(0.5, 0.5, "乙"),
                        AsrWord(1, 1, "。"),
                    ),
                ),
            ),
        ),
        1,
    )
    assert "".join(word.text for word in result.segments[0].words) == "甲乙。"


def test_inference_slot_is_exclusive_cancellable_and_released(paths):
    waiting = threading.Event()
    cancelled = threading.Event()
    entered = []

    def check():
        if cancelled.is_set():
            raise InterruptedError("cancel")

    def waiter():
        try:
            with asr_inference_slot(paths, check_cancelled=check, progress=lambda _: waiting.set()):
                entered.append(True)
        except InterruptedError:
            entered.append(False)

    with asr_inference_slot(paths):
        worker = threading.Thread(target=waiter)
        worker.start()
        assert waiting.wait(2)
        assert not entered
        cancelled.set()
        worker.join(2)
        assert not worker.is_alive()
        assert entered == [False]
    with asr_inference_slot(paths):
        pass


def test_silent_cli_process_can_be_cancelled_and_reaped(paths, monkeypatch):
    launched = []
    popen = subprocess.Popen

    def start(*args, **kwargs):
        process = popen(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", start)
    start_time = time.monotonic()

    def check():
        if time.monotonic() - start_time > 0.2:
            raise InterruptedError("cancel")

    with pytest.raises(InterruptedError):
        FasterWhisperCliEngine(AsrSettings(), paths, check_cancelled=check)._run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            None,
        )
    assert time.monotonic() - start_time < 3
    assert launched[0].poll() is not None


def test_prewarm_waits_for_transcription_and_cancel_does_not_load_model(paths, monkeypatch):
    monkeypatch.setattr(RuntimeToolService, "resolve_cli_path", lambda _: Path(sys.executable))
    monkeypatch.setattr(module.AudioChunkingService, "duration_seconds", lambda *_: 1)
    started = []
    monkeypatch.setattr(FasterWhisperCliEngine, "_transcribe_once", lambda *args, **kw: started.append(True))
    waiting = threading.Event()
    cancel = threading.Event()
    errors = []

    def report(value):
        if value.message_code == "asr_waiting_for_model":
            waiting.set()

    def check():
        if cancel.is_set():
            raise InterruptedError("cancel")

    def prewarm():
        try:
            RuntimeToolService(ServiceSettings(), paths).prewarm_cli(progress=report, check_cancelled=check)
        except BaseException as error:
            errors.append(error)

    with asr_inference_slot(paths):
        worker = threading.Thread(target=prewarm)
        worker.start()
        assert waiting.wait(2)
        cancel.set()
        worker.join(2)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], InterruptedError)
    assert not started


def test_cli_output_retains_only_bounded_diagnostic_tail(paths):
    code, lines = FasterWhisperCliEngine(AsrSettings(), paths)._run(
        [sys.executable, "-c", "for i in range(10000): print(i)"],
        None,
    )
    assert code == 0
    assert len(lines) == 200
    assert lines[0] == "9800" and lines[-1] == "9999"
