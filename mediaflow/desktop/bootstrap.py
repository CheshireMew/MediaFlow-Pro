from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil


def _service_state_root() -> Path:
    configured = os.environ.get("MEDIAFLOW_SERVICE_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise RuntimeError("LOCALAPPDATA is required for Editor Service discovery")
    return (Path(local) / "MediaFlow Pro" / "service").resolve()


def _service_is_live(discovery_path: Path) -> bool:
    try:
        payload = json.loads(discovery_path.read_text(encoding="utf-8"))
        process = psutil.Process(int(payload["pid"]))
        return (
            process.is_running()
            and process.status() not in {psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE}
            and abs(process.create_time() - float(payload["process_started_at"])) < 0.01
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError, psutil.Error):
        return False


def _start_service_warmup() -> tuple[subprocess.Popen[bytes] | None, Path]:
    discovery_path = _service_state_root() / "discovery.json"
    if _service_is_live(discovery_path):
        return None, discovery_path
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        # The desktop process is itself long-lived, so it can launch the resident
        # service directly without paying the WMI ownership hand-off used by
        # short-lived CLI clients. Windows does not tie a child process lifetime
        # to its parent, and a separate process group keeps normal desktop
        # shutdown signals from reaching the resident service.
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        start_new_session = True
    return subprocess.Popen(
        [sys.executable, "-m", "mediaflow.service"],
        cwd=Path.cwd(),
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=start_new_session,
    ), discovery_path


def main() -> int:
    warmup, discovery_path = _start_service_warmup()
    from mediaflow.desktop.app import main as application_main

    deadline = time.monotonic() + 15
    while not _service_is_live(discovery_path) and time.monotonic() < deadline:
        if warmup is not None and warmup.poll() not in {None, 0}:
            break
        time.sleep(0.025)
    return application_main()


if __name__ == "__main__":
    raise SystemExit(main())
