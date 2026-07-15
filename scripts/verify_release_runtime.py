from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = Path("D:/Tools/MediaFlow/test-runs")
FORBIDDEN_PROCESS_TERMS = ("node", "electron", "uvicorn", "fastapi")


def main() -> int:
    run_dir = RUN_ROOT / f"release-runtime-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment.setdefault("QT_QUICK_BACKEND", "software")
    process = subprocess.Popen(
        [sys.executable, "-m", "mediaflow.desktop.app"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 15
        root_process = psutil.Process(process.pid)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=2)
                raise RuntimeError(f"QML application exited during startup:\n{stdout}\n{stderr}")
            if root_process.status() in {psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING}:
                time.sleep(2)
                break
            time.sleep(0.1)

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
            "application_pid": process.pid,
            "application_running": process.poll() is None,
            "processes": process_rows,
            "listening_ports": listening,
            "forbidden_runtime_processes": forbidden,
            "passed": not listening and not forbidden and process.poll() is None,
        }
        report_path = run_dir / "release-runtime-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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


if __name__ == "__main__":
    raise SystemExit(main())
