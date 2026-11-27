from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from mediaflow.desktop.app import (
    STARTUP_READY_PATH_ENV,
    publish_startup_ready,
)
from scripts.verify_release_runtime import verify


def test_desktop_bootstrap_defers_project_models_and_command_contracts() -> None:
    code = textwrap.dedent(
        """
        import sys
        import mediaflow
        import mediaflow.desktop.bootstrap

        assert 'mediaflow.domain' not in sys.modules
        assert set(mediaflow.__all__) <= set(dir(mediaflow))
        assert not hasattr(mediaflow, 'unknown_public_type')

        def without_eager_command_models(frame, event, _argument):
            if (event == 'call'
                and frame.f_globals.get('__name__') == 'mediaflow.service.commands'
                and frame.f_code.co_name == 'command_models'):
                raise AssertionError('Startup compiled an unused command contract')

        sys.setprofile(without_eager_command_models)
        try:
            import mediaflow.desktop.app
            import mediaflow.service.server
        finally:
            sys.setprofile(None)

        from mediaflow import Project, ProjectProfile
        from mediaflow.domain.project import Project as CanonicalProject
        from mediaflow.domain.project import ProjectProfile as CanonicalProfile
        assert Project is CanonicalProject
        assert ProjectProfile is CanonicalProfile
        assert mediaflow.Project is Project
        """
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_editor_import_does_not_load_unused_llm_sdk() -> None:
    code = textwrap.dedent(
        """
        import sys
        import mediaflow.composition
        from mediaflow.domain.settings import LlmProviderSettings
        from mediaflow.infrastructure.llm_client import OpenAIJsonClient

        assert 'openai' not in sys.modules
        try:
            OpenAIJsonClient(LlmProviderSettings(
                name='Disabled', base_url='http://127.0.0.1:9/v1', model='unused', enabled=False,
            ))
        except ValueError as error:
            assert 'disabled' in str(error)
        else:
            raise AssertionError('Disabled provider was accepted')
        assert 'openai' not in sys.modules
        """
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
