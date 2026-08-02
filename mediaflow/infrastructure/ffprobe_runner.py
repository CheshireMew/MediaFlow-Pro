from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from mediaflow.infrastructure.subprocess_runner import run_cancellable


class FfprobeRunner:
    """The production boundary for constructing and running FFprobe commands."""

    def __init__(self, executable: str | Path) -> None:
        self.executable = Path(executable)
        self._creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def command(self, arguments: Sequence[str | Path]) -> list[str]:
        return [str(self.executable), *map(str, arguments)]

    def run(
        self,
        arguments: Sequence[str | Path],
        *,
        check_cancelled: Callable[[], None] | None = None,
        timeout: float | None = 30,
    ) -> subprocess.CompletedProcess[str]:
        result = run_cancellable(
            self.command(arguments),
            check_cancelled=check_cancelled,
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
