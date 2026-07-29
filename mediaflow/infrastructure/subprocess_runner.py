from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from queue import Empty, Queue


def run_cancellable(
    command: Sequence[str],
    *,
    check_cancelled: Callable[[], None] | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    timeout: float | None = None,
    creationflags: int = 0,
) -> subprocess.CompletedProcess:
    """Run a child process while keeping task cancellation observable."""
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding=encoding,
        errors=errors,
        creationflags=creationflags,
    )
    started = time.monotonic()
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                return subprocess.CompletedProcess(
                    args=list(command),
                    returncode=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
            except subprocess.TimeoutExpired as error:
                if check_cancelled:
                    check_cancelled()
                if timeout is not None and time.monotonic() - started > timeout:
                    raise subprocess.TimeoutExpired(command, timeout) from error
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        raise


def run_cancellable_streaming(
    command: Sequence[str],
    *,
    on_stdout_line: Callable[[str], None] | None = None,
    on_stderr_line: Callable[[str], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    encoding: str = "utf-8",
    errors: str = "replace",
    timeout: float | None = None,
    split_carriage_returns: bool = False,
    creationflags: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run a text process while consuming both output pipes continuously."""

    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=encoding,
        errors=errors,
        bufsize=1,
        creationflags=creationflags,
    )
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Streaming process pipes were not created")
    events: Queue[tuple[str, str | None]] = Queue()
    captured: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def consume(name: str, stream) -> None:
        try:
            if split_carriage_returns:
                buffered: list[str] = []
                while character := stream.read(1):
                    if character in {"\r", "\n"}:
                        if buffered:
                            events.put((name, "".join(buffered) + "\n"))
                            buffered.clear()
                    else:
                        buffered.append(character)
                if buffered:
                    events.put((name, "".join(buffered)))
            else:
                for line in iter(stream.readline, ""):
                    events.put((name, line))
        finally:
            stream.close()
            events.put((name, None))

    readers = [
        threading.Thread(
            target=consume,
            args=("stdout", process.stdout),
            name="mediaflow-process-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=consume,
            args=("stderr", process.stderr),
            name="mediaflow-process-stderr",
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    closed_streams = 0
    try:
        while closed_streams < 2 or process.poll() is None:
            if check_cancelled:
                check_cancelled()
            if timeout is not None and time.monotonic() - started > timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                name, line = events.get(timeout=0.1)
            except Empty:
                continue
            if line is None:
                closed_streams += 1
                continue
            captured[name].append(line)
            callback = on_stdout_line if name == "stdout" else on_stderr_line
            if callback is not None:
                callback(line.rstrip("\r\n"))
        returncode = process.wait()
        for reader in readers:
            reader.join(timeout=1)
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=returncode,
            stdout="".join(captured["stdout"]),
            stderr="".join(captured["stderr"]),
        )
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for reader in readers:
            reader.join(timeout=1)
        raise
