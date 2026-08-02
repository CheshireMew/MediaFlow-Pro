from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from mediaflow.infrastructure.subprocess_runner import (
    run_cancellable,
    run_cancellable_streaming,
)


@dataclass(frozen=True, slots=True)
class FfmpegPipeResult:
    returncode: int
    stderr: str


class FfmpegInputPipe:
    """A byte input pipe whose process lifecycle stays owned by the FFmpeg boundary."""

    def __init__(self, command: list[str], creationflags: int) -> None:
        self._command = command
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        if self._process.stdin is None or self._process.stderr is None:
            self.abort()
            raise RuntimeError("FFmpeg input pipe was not created")
        self._stderr_chunks: list[bytes] = []
        self._stderr_reader = threading.Thread(
            target=self._read_stderr,
            name="mediaflow-ffmpeg-stderr",
            daemon=True,
        )
        self._stderr_reader.start()
        self._finished = False

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        try:
            while chunk := self._process.stderr.read(64 * 1024):
                self._stderr_chunks.append(chunk)
        finally:
            self._process.stderr.close()

    def write(self, data: bytes) -> None:
        if self._finished:
            raise RuntimeError("FFmpeg input pipe is already closed")
        assert self._process.stdin is not None
        self._process.stdin.write(data)

    def finish(self, *, timeout: float | None = None) -> FfmpegPipeResult:
        if self._finished:
            raise RuntimeError("FFmpeg input pipe is already closed")
        self._finished = True
        assert self._process.stdin is not None
        self._process.stdin.close()
        try:
            returncode = self._process.wait(timeout=timeout)
        except BaseException:
            self._stop_process()
            raise
        finally:
            self._stderr_reader.join(timeout=2)
        return FfmpegPipeResult(
            returncode=returncode,
            stderr=b"".join(self._stderr_chunks).decode("utf-8", errors="replace"),
        )

    def abort(self) -> None:
        if not self._finished:
            self._finished = True
            if self._process.stdin is not None:
                self._process.stdin.close()
        self._stop_process()
        self._stderr_reader.join(timeout=2)

    def _stop_process(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()


class FfmpegOutputPipe:
    """A byte output pipe whose process lifecycle stays owned by the FFmpeg boundary."""

    def __init__(self, command: list[str], creationflags: int) -> None:
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        if self._process.stdout is None or self._process.stderr is None:
            self.abort()
            raise RuntimeError("FFmpeg output pipe was not created")
        self._stderr_chunks: list[bytes] = []
        self._stderr_reader = threading.Thread(
            target=self._read_stderr,
            name="mediaflow-ffmpeg-stderr",
            daemon=True,
        )
        self._stderr_reader.start()
        self._finished = False

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        try:
            while chunk := self._process.stderr.read(64 * 1024):
                self._stderr_chunks.append(chunk)
        finally:
            self._process.stderr.close()

    def read(self, size: int) -> bytes:
        if self._finished:
            raise RuntimeError("FFmpeg output pipe is already closed")
        if size <= 0:
            raise ValueError("FFmpeg output pipe read size must be positive")
        assert self._process.stdout is not None
        return self._process.stdout.read(size)

    def finish(self, *, timeout: float | None = None) -> FfmpegPipeResult:
        if self._finished:
            raise RuntimeError("FFmpeg output pipe is already closed")
        self._finished = True
        assert self._process.stdout is not None
        self._process.stdout.close()
        try:
            returncode = self._process.wait(timeout=timeout)
        except BaseException:
            self._stop_process()
            raise
        finally:
            self._stderr_reader.join(timeout=2)
        return FfmpegPipeResult(
            returncode=returncode,
            stderr=b"".join(self._stderr_chunks).decode("utf-8", errors="replace"),
        )

    def abort(self) -> None:
        if not self._finished:
            self._finished = True
            if self._process.stdout is not None:
                self._process.stdout.close()
        self._stop_process()
        self._stderr_reader.join(timeout=2)

    def _stop_process(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()


class _FfmpegProgressParser:
    def __init__(self, total_seconds: float, on_position: Callable[[float], None]):
        if total_seconds <= 0:
            raise ValueError("FFmpeg progress requires a positive media duration")
        self.total_seconds = float(total_seconds)
        self.on_position = on_position

    def __call__(self, line: str) -> None:
        key, separator, value = line.partition("=")
        if not separator:
            return
        seconds: float | None = None
        if key in {"out_time_us", "out_time_ms"}:
            try:
                seconds = int(value) / 1_000_000.0
            except ValueError:
                return
        elif key == "out_time":
            parts = value.split(":")
            if len(parts) != 3:
                return
            try:
                hours, minutes, raw_seconds = parts
                seconds = int(hours) * 3600 + int(minutes) * 60 + float(raw_seconds)
            except ValueError:
                return
        elif key == "progress" and value == "end":
            seconds = self.total_seconds
        if seconds is not None:
            self.on_position(max(0.0, min(self.total_seconds, seconds)))


class FfmpegRunner:
    """The only production boundary that constructs and starts FFmpeg processes."""

    def __init__(self, executable: str | Path) -> None:
        self.executable = Path(executable)
        self._creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def command(
        self,
        arguments: Sequence[str | Path],
        *,
        progress_protocol: bool = False,
    ) -> list[str]:
        prefix = [str(self.executable), "-hide_banner"]
        if progress_protocol:
            prefix.extend(["-nostats", "-progress", "pipe:2"])
        return [*prefix, *map(str, arguments)]

    def run(
        self,
        arguments: Sequence[str | Path],
        *,
        check_cancelled: Callable[[], None] | None = None,
        cwd: str | Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = run_cancellable(
            self.command(arguments),
            check_cancelled=check_cancelled,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=self._creationflags,
        )
        return subprocess.CompletedProcess(
            args=result.args,
            returncode=result.returncode,
            stdout=str(result.stdout or ""),
            stderr=str(result.stderr or ""),
        )

    def run_progress(
        self,
        arguments: Sequence[str | Path],
        *,
        total_seconds: float | None,
        on_position: Callable[[float], None] | None = None,
        on_stdout_line: Callable[[str], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
        cwd: str | Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        parser = (
            _FfmpegProgressParser(total_seconds, on_position)
            if total_seconds is not None and total_seconds > 0 and on_position is not None
            else None
        )

        def consume_stderr(line: str) -> None:
            if parser is not None:
                parser(line)
            if on_stderr_line is not None:
                on_stderr_line(line)

        return run_cancellable_streaming(
            self.command(arguments, progress_protocol=True),
            on_stdout_line=on_stdout_line,
            on_stderr_line=consume_stderr,
            check_cancelled=check_cancelled,
            cwd=cwd,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=self._creationflags,
        )

    def open_input_pipe(self, arguments: Sequence[str | Path]) -> FfmpegInputPipe:
        return FfmpegInputPipe(
            self.command(arguments),
            self._creationflags,
        )

    def open_output_pipe(self, arguments: Sequence[str | Path]) -> FfmpegOutputPipe:
        return FfmpegOutputPipe(
            self.command(arguments),
            self._creationflags,
        )
