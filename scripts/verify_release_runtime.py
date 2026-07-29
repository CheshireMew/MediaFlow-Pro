# ruff: noqa: E402

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.desktop.app import (
    STARTUP_READY_PATH_ENV,
    STARTUP_READY_SCHEMA_VERSION,
)
from scripts.run_artifacts import verification_run

FORBIDDEN_PROCESS_TERMS = ("node", "electron", "uvicorn", "fastapi")


def _wait_for_startup_ready(
    process: subprocess.Popen[str],
    ready_path: Path,
    *,
    started_at_ns: int,
    timeout_seconds: float,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise RuntimeError(
                "QML application exited before startup readiness:\n"
                f"{stdout}\n{stderr}"
            )
        if not ready_path.is_file():
            time.sleep(0.05)
            continue
        try:
            payload = json.loads(ready_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError) as error:
            raise RuntimeError(
                f"Startup readiness evidence is unreadable: {ready_path}"
            ) from error
        observed_at_ns = time.time_ns()
        if not isinstance(payload, dict):
            raise RuntimeError("Startup readiness evidence must be a JSON object")
        if set(payload) != {
            "schema_version",
            "pid",
            "ready_at_ns",
            "root_object_count",
            "event_loop_processed",
        }:
            raise RuntimeError("Startup readiness evidence has an unexpected schema")
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != STARTUP_READY_SCHEMA_VERSION
            or type(payload["pid"]) is not int
            or type(payload["ready_at_ns"]) is not int
            or not started_at_ns <= payload["ready_at_ns"] <= observed_at_ns
            or type(payload["root_object_count"]) is not int
            or payload["root_object_count"] < 1
            or payload["event_loop_processed"] is not True
        ):
            raise RuntimeError(
                "Startup readiness evidence does not match the launched process"
            )
        try:
            launcher = psutil.Process(process.pid)
            owned_pids = {
                launcher.pid,
                *(
                    child.pid
                    for child in launcher.children(recursive=True)
                ),
            }
        except psutil.NoSuchProcess as error:
            raise RuntimeError(
                "Startup launcher disappeared while validating readiness"
            ) from error
        if payload["pid"] not in owned_pids:
            raise RuntimeError(
                "Startup readiness PID is outside the launched process tree"
            )
        return payload
    raise TimeoutError(
        "QML application stayed alive but produced no startup readiness evidence"
    )


def verify(
    run_dir: Path,
    *,
    command: list[str] | None = None,
    startup_timeout_seconds: float = 15,
) -> int:
    if startup_timeout_seconds <= 0:
        raise ValueError("Startup timeout must be positive")
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment.setdefault("QT_QUICK_BACKEND", "software")
    ready_path = run_dir / "startup-ready.json"
    if ready_path.exists():
        raise RuntimeError(
            f"Startup readiness path already exists: {ready_path}"
        )
    environment[STARTUP_READY_PATH_ENV] = str(ready_path)
    started_at_ns = time.time_ns()
    process = subprocess.Popen(
        command or [sys.executable, "-m", "mediaflow.desktop.app"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        startup_ready = _wait_for_startup_ready(
            process,
            ready_path,
            started_at_ns=started_at_ns,
            timeout_seconds=startup_timeout_seconds,
        )
        root_process = psutil.Process(process.pid)
        application_process = psutil.Process(
            int(startup_ready["pid"])
        )

        inspected = [root_process, *root_process.children(recursive=True)]
        process_rows: list[dict[str, object]] = []
        listening: list[dict[str, object]] = []
        forbidden: list[str] = []
        for item in inspected:
            try:
                name = item.name()
                command = " ".join(item.cmdline())
                process_rows.append({"pid": item.pid, "name": name, "command": command})
                normalized = f"{name} {command}".lower()
                if item.pid != root_process.pid and any(
                    term in normalized for term in FORBIDDEN_PROCESS_TERMS
                ):
                    forbidden.append(normalized)
                for connection in item.net_connections(kind="inet"):
                    if connection.status == psutil.CONN_LISTEN:
                        listening.append(
                            {
                                "pid": item.pid,
                                "address": list(connection.laddr),
                            }
                        )
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue

        report = {
            "launcher_pid": process.pid,
            "application_pid": application_process.pid,
            "application_running": (
                process.poll() is None
                and application_process.is_running()
                and application_process.status()
                != psutil.STATUS_ZOMBIE
            ),
            "startup_ready_path": str(ready_path),
            "startup_ready": startup_ready,
            "processes": process_rows,
            "listening_ports": listening,
            "forbidden_runtime_processes": forbidden,
            "passed": (
                not listening
                and not forbidden
                and process.poll() is None
                and application_process.is_running()
                and application_process.status()
                != psutil.STATUS_ZOMBIE
            ),
        }
        report_path = run_dir / "release-runtime-report.json"
        atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
        print(report_path)
        if not report["passed"]:
            raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    with verification_run("release-runtime") as run_dir:
        return verify(run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
