from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_artifacts
from scripts.run_artifacts import (
    MANIFEST_FILENAME,
    _remove_managed_run,
    verification_run,
)
from tests.v2 import conftest as pytest_retention


def _manifest(run_dir: Path) -> dict[str, object]:
    payload = json.loads(
        (run_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def _hold_file_without_delete_sharing(path: Path) -> subprocess.Popen[str]:
    script = "\n".join(
        [
            "import ctypes",
            "import sys",
            "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)",
            "kernel32.CreateFileW.argtypes = [",
            "    ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,",
            "    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,",
            "    ctypes.c_void_p,",
            "]",
            "kernel32.CreateFileW.restype = ctypes.c_void_p",
            "handle = kernel32.CreateFileW(sys.argv[1], 0x80000000, 0x1, None, 3, 0x80, None)",
            "if handle == ctypes.c_void_p(-1).value:",
            "    raise OSError(ctypes.get_last_error(), 'CreateFileW failed')",
            "print('ready', flush=True)",
            "sys.stdin.readline()",
            "kernel32.CloseHandle(handle)",
        ]
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def _release_file_holder(process: subprocess.Popen[str]) -> None:
    assert process.stdin is not None
    process.stdin.write("\n")
    process.stdin.flush()
    stdout, stderr = process.communicate(timeout=5)
    assert (process.returncode, stdout, stderr) == (0, "", "")


def test_script_run_retention_keeps_failures_interruptions_and_latest_success(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "managed-scripts"
    previous_settings = os.environ.get("MEDIAFLOW_SETTINGS_PATH")
    previous_media_root = os.environ.get("MEDIAFLOW_MEDIA_ROOT")
    previous_project_root = os.environ.get("MEDIAFLOW_PROJECT_ROOT")

    with verification_run("policy", managed_root=managed_root) as first_success:
        assert Path(os.environ["MEDIAFLOW_SETTINGS_PATH"]).is_relative_to(
            first_success
        )
        assert Path(os.environ["MEDIAFLOW_MEDIA_ROOT"]).is_relative_to(
            first_success
        )
        assert Path(os.environ["MEDIAFLOW_PROJECT_ROOT"]).is_relative_to(first_success)
        (first_success / "success.txt").write_text("first", encoding="utf-8")
    assert _manifest(first_success)["status"] == "passed"
    assert os.environ.get("MEDIAFLOW_SETTINGS_PATH") == previous_settings
    assert os.environ.get("MEDIAFLOW_MEDIA_ROOT") == previous_media_root
    assert os.environ.get("MEDIAFLOW_PROJECT_ROOT") == previous_project_root

    with pytest.raises(RuntimeError, match="deliberate failure"):
        with verification_run("policy", managed_root=managed_root) as failed:
            (failed / "failure.txt").write_text("keep me", encoding="utf-8")
            raise RuntimeError("deliberate failure")
    failed_manifest = _manifest(failed)
    assert failed_manifest["status"] == "failed"
    assert failed_manifest["error"]["type"] == "RuntimeError"

    interrupted_run = verification_run("policy", managed_root=managed_root)
    interrupted = interrupted_run.path
    (interrupted / "interrupted.txt").write_text("keep me too", encoding="utf-8")
    assert _manifest(interrupted)["status"] == "running"

    category_root = managed_root / "policy"
    historical = category_root / "policy-legacy-unclassified"
    historical.mkdir()
    (historical / "unknown.txt").write_text("do not classify", encoding="utf-8")

    explicit_root = tmp_path / "chosen-root"
    with verification_run("policy", explicit_root=explicit_root):
        pass
    explicit_parent = tmp_path / "chosen-parent"
    with verification_run(
        "policy",
        explicit_parent=explicit_parent,
    ) as explicit_child:
        pass

    with verification_run("policy", managed_root=managed_root) as latest_success:
        (latest_success / "success.txt").write_text("latest", encoding="utf-8")

    assert not first_success.exists()
    assert latest_success.is_dir()
    assert _manifest(latest_success)["status"] == "passed"
    assert failed.is_dir()
    assert interrupted.is_dir()
    assert historical.is_dir()
    assert explicit_root.is_dir()
    assert explicit_child.is_dir()
    assert _manifest(explicit_root)["managed"] is False
    assert _manifest(explicit_child)["managed"] is False

    with pytest.raises(RuntimeError, match="unmanaged verification path"):
        _remove_managed_run(category_root, category_root, "policy")
    with pytest.raises(RuntimeError, match="unmanaged verification path"):
        _remove_managed_run(category_root, historical, "policy")


def test_managed_script_manifest_failure_archives_unpublished_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_root = tmp_path / "managed-scripts"

    def fail_manifest(*_args, **_kwargs) -> None:
        raise OSError("injected script manifest failure")

    monkeypatch.setattr(
        run_artifacts,
        "_write_manifest",
        fail_manifest,
    )
    with pytest.raises(
        OSError,
        match="script manifest failure",
    ):
        verification_run(
            "policy",
            managed_root=managed_root,
        )

    category_root = managed_root / "policy"
    published_runs = [
        path
        for path in category_root.iterdir()
        if path.is_dir()
        and path.name != "setup-failures"
    ]
    assert published_runs == []
    archived = list(
        (category_root / "setup-failures").iterdir()
    )
    assert len(archived) == 1
    assert archived[0].is_dir()


def test_script_run_retention_is_cross_process_safe(tmp_path: Path) -> None:
    managed_root = tmp_path / "concurrent-scripts"
    worker = "\n".join(
        [
            "import sys",
            "import time",
            "from pathlib import Path",
            "from scripts.run_artifacts import verification_run",
            "with verification_run('concurrent', managed_root=Path(sys.argv[1])) as run_dir:",
            "    (run_dir / 'worker.txt').write_text(sys.argv[2], encoding='utf-8')",
            "    time.sleep(float(sys.argv[3]))",
        ]
    )
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                worker,
                str(managed_root),
                worker_name,
                delay,
            ],
            cwd=Path(__file__).resolve().parents[3],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for worker_name, delay in (("first", "0.2"), ("second", "0.1"))
    ]
    completed = [process.communicate(timeout=10) for process in processes]
    assert [
        (process.returncode, stdout, stderr)
        for process, (stdout, stderr) in zip(processes, completed, strict=True)
    ] == [(0, "", ""), (0, "", "")]

    category_root = managed_root / "concurrent"
    retained = [path for path in category_root.iterdir() if path.is_dir()]
    assert len(retained) == 1
    assert _manifest(retained[0])["status"] == "passed"
    assert (category_root / ".lifecycle.lock").is_file()


def test_pytest_retention_preserves_status_when_windows_files_are_still_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_root = tmp_path / "managed-pytest"
    managed_root.mkdir()
    monkeypatch.setattr(
        pytest_retention,
        "MANAGED_PYTEST_ROOT",
        managed_root,
    )
    legacy_success = (
        managed_root
        / "pytest-run-20260728T040000.000000Z-100-aaaaaaaa"
    )
    (legacy_success / "cases" / "legacy-case").mkdir(
        parents=True
    )
    (
        legacy_success
        / "cases"
        / "legacy-case"
        / "result.txt"
    ).write_text("old success", encoding="utf-8")
    (
        legacy_success / "run-result.json"
    ).write_text(
        json.dumps(
            {
                "status": "passed",
                "finished_at": (
                    "2026-07-28T04:00:00+00:00"
                ),
                "process_id": 100,
                "exit_status": 0,
                "failed_nodes": [],
                "case_count": 1,
                "retained_nodes": [],
                "retained_case_count": 1,
            }
        ),
        encoding="utf-8",
    )
    legacy_failure = (
        managed_root
        / "pytest-run-20260728T040100.000000Z-101-bbbbbbbb"
    )
    (legacy_failure / "cases" / "failed-case").mkdir(
        parents=True
    )
    (
        legacy_failure / "run-result.json"
    ).write_text(
        json.dumps(
            {
                "status": "failed",
                "finished_at": (
                    "2026-07-28T04:01:00+00:00"
                ),
                "process_id": 101,
                "exit_status": 1,
                "failed_nodes": ["suite::failed"],
                "case_count": 1,
            }
        ),
        encoding="utf-8",
    )

    failed_root = managed_root / "r-20260728T050000-101-aaaaaaaa"
    passed_case = failed_root / "cases" / "c-passed"
    failed_case = failed_root / "cases" / "c-failed"
    passed_case.mkdir(parents=True)
    failed_case.mkdir()
    locked_case_file = passed_case / "still-open.bin"
    locked_case_file.write_bytes(b"locked")
    (failed_case / "failure.txt").write_text(
        "retain failure",
        encoding="utf-8",
    )
    failed_state = pytest_retention.TestRunState(
        failed_root,
        cases={
            "suite::passed": passed_case,
            "suite::failed": failed_case,
        },
        failed_nodes={"suite::failed"},
        finished_nodes={"suite::passed", "suite::failed"},
    )
    holder = _hold_file_without_delete_sharing(locked_case_file)
    try:
        pytest_retention._finish_failed_run(
            failed_state,
            pytest.ExitCode.TESTS_FAILED,
        )
    finally:
        _release_file_holder(holder)

    failed_manifest = _manifest(failed_root)
    assert failed_manifest["status"] == "failed"
    assert failed_manifest["exit_status"] == int(
        pytest.ExitCode.TESTS_FAILED
    )
    assert failed_manifest["retention_status"] == "incomplete"
    assert set(failed_manifest["retained_nodes"]) == {
        "suite::failed",
        "suite::passed",
    }
    assert failed_manifest["retention_errors"][0]["node_id"] == (
        "suite::passed"
    )

    first_root = managed_root / "r-20260728T050100-102-bbbbbbbb"
    (first_root / "cases").mkdir(parents=True)
    first_state = pytest_retention.TestRunState(first_root)
    pytest_retention._finish_passed_run(
        first_state,
        pytest.ExitCode.OK,
    )
    assert not legacy_success.exists()
    assert legacy_failure.is_dir()
    first_manifest_path = first_root / "run-result.json"
    success_holder = _hold_file_without_delete_sharing(first_manifest_path)
    second_root = managed_root / "r-20260728T050200-103-cccccccc"
    (second_root / "cases").mkdir(parents=True)
    try:
        pytest_retention._finish_passed_run(
            pytest_retention.TestRunState(second_root),
            pytest.ExitCode.OK,
        )
    finally:
        _release_file_holder(success_holder)

    second_manifest = _manifest(second_root)
    assert second_manifest["status"] == "passed"
    assert second_manifest["exit_status"] == int(pytest.ExitCode.OK)
    assert second_manifest["retention_status"] == "incomplete"
    assert first_root.exists()

    third_root = managed_root / "r-20260728T050300-104-dddddddd"
    (third_root / "cases").mkdir(parents=True)
    pytest_retention._finish_passed_run(
        pytest_retention.TestRunState(third_root),
        pytest.ExitCode.OK,
    )

    assert not first_root.exists()
    assert not second_root.exists()
    assert third_root.is_dir()
    assert _manifest(third_root)["retention_status"] == "complete"


def test_pytest_manifest_creation_failure_archives_unpublished_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_root = tmp_path / "managed-pytest"
    monkeypatch.setattr(
        pytest_retention,
        "MANAGED_PYTEST_ROOT",
        managed_root,
    )

    def fail_manifest(*_args, **_kwargs) -> None:
        raise OSError("injected pytest manifest failure")

    monkeypatch.setattr(
        pytest_retention,
        "_write_manifest",
        fail_manifest,
    )
    config = SimpleNamespace(stash={})
    with pytest.raises(
        OSError,
        match="pytest manifest failure",
    ):
        pytest_retention.pytest_configure(config)

    published_runs = [
        path
        for path in managed_root.iterdir()
        if path.is_dir()
        and path.name != "setup-failures"
    ]
    assert published_runs == []
    archived = list(
        (managed_root / "setup-failures").iterdir()
    )
    assert len(archived) == 1
    assert archived[0].is_dir()
