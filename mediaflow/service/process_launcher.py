from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def launch_editor_service(
    *,
    working_directory: Path,
    log_path: Path,
) -> subprocess.Popen[bytes] | None:
    """Start the per-user service outside short-lived client process trees."""

    environment = os.environ.copy()
    environment.setdefault("PYTHONFAULTHANDLER", "1")
    environment.setdefault("PYTHONUNBUFFERED", "1")
    command = [sys.executable, "-m", "mediaflow.service"]
    if sys.platform == "win32":
        helper_environment = environment.copy()
        helper_environment["_MEDIAFLOW_SERVICE_PYTHONFAULTHANDLER"] = (
            helper_environment.pop("PYTHONFAULTHANDLER")
        )
        completed = subprocess.run(
            [sys.executable, "-m", "mediaflow.service.windows_launcher"],
            cwd=working_directory,
            env=helper_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise OSError(detail or "Windows could not start MediaFlow Editor Service")
        return None

    with log_path.open("a", encoding="utf-8") as log:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            cwd=working_directory,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
