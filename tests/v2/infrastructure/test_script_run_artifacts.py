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
    VERIFICATION_WORK_ROOT_VARIABLE,
    verification_run,
    verification_workspace_root,
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


def test_script_run_retention_preserves_every_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(VERIFICATION_WORK_ROOT_VARIABLE, raising=False)
    managed_root = tmp_path / "managed-scripts"
    previous_service_settings = os.environ.get("MEDIAFLOW_SERVICE_SETTINGS_PATH")
    previous_desktop_settings = os.environ.get("MEDIAFLOW_DESKTOP_SETTINGS_PATH")
    previous_media_root = os.environ.get("MEDIAFLOW_MEDIA_ROOT")
    previous_project_root = os.environ.get("MEDIAFLOW_PROJECT_ROOT")

    with verification_run("policy", managed_root=managed_root) as first_success:
        assert Path(os.environ["MEDIAFLOW_SERVICE_SETTINGS_PATH"]).is_relative_to(
            first_success
        )
        assert Path(os.environ["MEDIAFLOW_DESKTOP_SETTINGS_PATH"]).is_relative_to(
            first_success
        )
        assert Path(os.environ["MEDIAFLOW_MEDIA_ROOT"]).is_relative_to(
            first_success
        )
        assert Path(os.environ["MEDIAFLOW_PROJECT_ROOT"]).is_relative_to(first_success)
        (first_success / "success.txt").write_text("first", encoding="utf-8")
    assert _manifest(first_success)["status"] == "passed"
    assert (
        os.environ.get("MEDIAFLOW_SERVICE_SETTINGS_PATH")
        == previous_service_settings
    )
    assert (
        os.environ.get("MEDIAFLOW_DESKTOP_SETTINGS_PATH")
        == previous_desktop_settings
    )
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

    assert first_success.is_dir()
    assert latest_success.is_dir()
    assert _manifest(latest_success)["status"] == "passed"
    assert failed.is_dir()
    assert interrupted.is_dir()
    assert historical.is_dir()
    assert explicit_root.is_dir()
    assert explicit_child.is_dir()
    assert _manifest(explicit_root)["managed"] is False
    assert _manifest(explicit_child)["managed"] is False

    assert _manifest(first_success)["storage"]["cleanup"] == "report-only-until-authorized"
    assert (first_success / "success.txt").read_text(encoding="utf-8") == "first"


def test_verification_run_separates_long_evidence_from_short_native_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "deep" / "evidence" / "run"
    workspace_base = tmp_path / "short"
    monkeypatch.setenv(VERIFICATION_WORK_ROOT_VARIABLE, str(workspace_base))
    previous_service_state = os.environ.get("MEDIAFLOW_SERVICE_STATE_DIR")

    with verification_run("policy", explicit_root=evidence) as run_dir:
        workspace = verification_workspace_root(run_dir)
        assert workspace.parent == workspace_base.resolve()
        assert Path(os.environ["MEDIAFLOW_PROJECT_ROOT"]).is_relative_to(workspace)
        assert Path(os.environ["MEDIAFLOW_MEDIA_ROOT"]).is_relative_to(workspace)
        assert Path(os.environ["MEDIAFLOW_SERVICE_STATE_DIR"]).is_relative_to(workspace)
        assert run_dir == evidence.resolve()

    assert os.environ.get("MEDIAFLOW_SERVICE_STATE_DIR") == previous_service_state


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
    assert len(retained) == 2
    assert all(_manifest(path)["status"] == "passed" for path in retained)
    assert (category_root / ".lifecycle.lock").is_file()


@pytest.mark.parametrize("exitstatus", [pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED])
def test_pytest_retention_preserves_failures_locked_files_and_unregistered_prior_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exitstatus: pytest.ExitCode,
) -> None:
    managed_root = tmp_path / "p"
    monkeypatch.setattr(pytest_retention, "MANAGED_PYTEST_ROOT", managed_root)
    prior = managed_root / "prior-run" / "result.txt"
    prior.parent.mkdir(parents=True)
    prior.write_text("retain prior evidence", encoding="utf-8")
    root = managed_root / "r-000000000001"
    passed = root / "c" / "c-000000000001"
    failed = root / "c" / "c-000000000002"
    passed.mkdir(parents=True)
    failed.mkdir()
    locked = passed / "still-open.bin"
    locked.write_bytes(b"locked")
    state = pytest_retention.TestRunState(
        root,
        cases={"suite::passed": passed, "suite::failed": failed},
        failed_nodes={"suite::failed"} if exitstatus else set(),
        finished_nodes={"suite::passed", "suite::failed"},
    )
    (root / "run-result.json").write_text(
        json.dumps({"schema": pytest_retention.MANIFEST_SCHEMA, "run_id": root.name,
                    "managed": True, "status": "running", "process_id": os.getpid()}),
        encoding="utf-8",
    )
    holder = _hold_file_without_delete_sharing(locked)
    try:
        with pytest.warns(pytest.PytestWarning, match="cleanup was incomplete"):
            pytest_retention._finish_run(state, exitstatus)
    finally:
        _release_file_holder(holder)
    manifest = _manifest(root)
    assert manifest["status"] == ("passed" if not exitstatus else "failed")
    assert manifest["exit_status"] == int(exitstatus)
    assert manifest["retention_status"] == "incomplete"
    assert manifest["retention_errors"]
    assert set(manifest["retained_nodes"]) == (
        {"suite::passed", "suite::failed"} if exitstatus else {"suite::passed"}
    )
    assert prior.read_text(encoding="utf-8") == "retain prior evidence"
    assert locked.read_bytes() == b"locked"
    assert failed.is_dir() == bool(exitstatus)


def test_pytest_runner_retains_only_failed_case_after_collection_success_and_failure(tmp_path: Path) -> None:
    source = tmp_path / "test_retention_case.py"
    source.write_text(
        "import os\n"
        "def test_evidence(tmp_path):\n"
        "    (tmp_path / 'result.txt').write_text('retained')\n"
        "    assert os.environ.get('FAIL_RETENTION_CASE') != '1'\n",
        encoding="utf-8",
    )
    evidence_root = tmp_path / "child-runs"
    environment = {**os.environ, "MEDIAFLOW_TEST_ROOT": str(evidence_root)}
    command = [sys.executable, "-B", "-m", "pytest", "-p", "tests.v2.conftest",
               "--noconftest", "-p", "no:cacheprovider", "-q", str(source)]
    before_files: dict[Path, bytes] = {}
    for extra, expected, failure in [(["--collect-only"], 0, False), ([], 0, False), ([], 1, True)]:
        completed = subprocess.run(
            [*command, *extra],
            env={**environment, "FAIL_RETENTION_CASE": "1" if failure else "0"},
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert completed.returncode == expected, completed.stdout + completed.stderr
        assert all(path.read_bytes() == contents for path, contents in before_files.items())
        before_files = {path: path.read_bytes() for path in evidence_root.rglob("result.txt")}
    manifests = [json.loads(path.read_text(encoding="utf-8"))
                 for path in (evidence_root / "p").glob("r-*/run-result.json")]
    assert len(manifests) == 3
    assert sorted(item["status"] for item in manifests) == ["failed", "passed", "passed"]
    assert len(before_files) == 1
    assert all(item["retained_case_count"] == 0 for item in manifests if item["status"] == "passed")



def test_pytest_manifest_creation_failure_archives_unpublished_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_root = tmp_path / "p"
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
