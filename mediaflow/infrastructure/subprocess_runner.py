from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path


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
