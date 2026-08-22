from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mediaflow.desktop.app import (
    STARTUP_READY_PATH_ENV,
    publish_startup_ready,
)
from scripts.verify_release_runtime import verify


def test_startup_readiness_evidence_is_strictly_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedEngineAccess:
        @staticmethod
        def rootObjects():
            raise AssertionError("Normal startup must not build readiness evidence")

    monkeypatch.delenv(STARTUP_READY_PATH_ENV, raising=False)

    assert publish_startup_ready(UnexpectedEngineAccess()) is None


def test_release_runtime_rejects_a_live_process_that_never_becomes_ready(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        TimeoutError,
        match="produced no startup readiness evidence",
    ):
        verify(
            tmp_path,
            command=[
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ],
            startup_timeout_seconds=0.2,
        )

    assert not (tmp_path / "startup-ready.json").exists()
    assert not (tmp_path / "release-runtime-report.json").exists()


def test_release_runtime_reads_ready_evidence_from_the_real_desktop_startup(
    tmp_path: Path,
) -> None:
    assert verify(tmp_path, startup_timeout_seconds=30) == 0

    ready_path = tmp_path / "startup-ready.json"
    report_path = tmp_path / "release-runtime-report.json"
    ready = json.loads(ready_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert ready["pid"] == report["application_pid"]
    assert ready["root_object_count"] >= 1
    assert ready["event_loop_processed"] is True
    assert report["startup_ready"] == ready
    assert report["startup_ready_path"] == str(ready_path)
    assert report["startup_seconds"] <= report["maximum_startup_seconds"]
    assert report["application_running"] is True
    assert report["desktop_shutdown_verified"] is True
    assert report["resident_service_after_desktop_shutdown"] is True
    assert report["editor_service_listener_verified"] is True
    assert report["unexpected_listening_ports"] == []
    assert report["passed"] is True
