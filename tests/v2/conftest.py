from __future__ import annotations

import hashlib
import json
import msvcrt
import os
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import pytest

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.storage_names import (
    PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    PROJECT_ROOT_PATH_UTF16_LIMIT,
    safe_child_path,
    utf16_units,
)

os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

TEST_RUN_ROOT = Path("D:/Tools/MediaFlow/test-runs")
MANAGED_PYTEST_ROOT = TEST_RUN_ROOT / "pytest"
MANIFEST_SCHEMA = "mediaflow-pytest-run/v1"
_LEGACY_MANAGED_RUN_PATTERN = re.compile(
    r"^(?:"
    r"r-\d{8}T\d{6}-\d+-[0-9a-f]{8}"
    r"|pytest-run-\d{8}T\d{6}\.\d{6}Z-\d+-[0-9a-f]{8}"
    r")$"
)
RUN_STATE_KEY: pytest.StashKey[TestRunState] = pytest.StashKey()
_REMOVE_RETRY_DELAYS_SECONDS = (0.0, 0.05, 0.1, 0.2, 0.4)


@dataclass
class TestRunState:
    root: Path
    cases: dict[str, Path] = field(default_factory=dict)
    failed_nodes: set[str] = field(default_factory=set)
    finished_nodes: set[str] = field(default_factory=set)


def _safe_case_name(node_id: str) -> str:
    # The node id remains in the manifest. Keeping it out of the directory name
    # leaves enough path budget for projects with deeply nested managed sources
    # on Windows.
    digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
    return f"c-{digest}"


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _remove_managed_directory(path: Path) -> None:
    managed_root = MANAGED_PYTEST_ROOT.resolve()
    resolved = path.resolve()
    if resolved == managed_root or not resolved.is_relative_to(managed_root):
        raise RuntimeError(f"Refusing to remove unmanaged test path: {resolved}")
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise RuntimeError(f"Refusing to follow a linked test path: {path}")
    if not path.is_dir():
        return
    last_error: OSError | None = None
    for delay in _REMOVE_RETRY_DELAYS_SECONDS:
        if delay:
            time.sleep(delay)
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError as error:
            last_error = error
        except OSError as error:
            if getattr(error, "winerror", None) != 32:
                raise
            last_error = error
    if last_error is not None:
        raise last_error


def _retention_error(
    path: Path,
    error: BaseException,
    *,
    node_id: str | None = None,
) -> dict[str, str]:
    payload = {
        "path": str(path),
        "type": type(error).__name__,
        "message": str(error),
    }
    if node_id is not None:
        payload["node_id"] = node_id
    return payload


def _try_remove_managed_directory(
    path: Path,
    *,
    node_id: str | None = None,
) -> dict[str, str] | None:
    try:
        _remove_managed_directory(path)
    except Exception as error:
        return _retention_error(path, error, node_id=node_id)
    return None


def _owned_passed_manifest(candidate: Path) -> dict[str, object]:
    managed_root = MANAGED_PYTEST_ROOT.resolve(strict=True)
    if candidate.is_symlink() or (
        hasattr(candidate, "is_junction") and candidate.is_junction()
    ):
        raise RuntimeError(f"Refusing to follow a linked pytest run: {candidate}")
    resolved = candidate.resolve(strict=True)
    if (
        resolved.parent != managed_root
        or not _LEGACY_MANAGED_RUN_PATTERN.fullmatch(
            candidate.name
        )
    ):
        raise RuntimeError(f"Refusing to inspect an unmanaged pytest run: {resolved}")
    manifest = candidate / "run-result.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid pytest run manifest: {manifest}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"Pytest run ownership mismatch: {candidate}")
    if payload.get("schema") == MANIFEST_SCHEMA:
        expected = {
            "run_id": candidate.name,
            "managed": True,
            "status": "passed",
        }
        if any(
            payload.get(key) != value
            for key, value in expected.items()
        ):
            raise RuntimeError(
                f"Pytest run ownership mismatch: {candidate}"
            )
    elif not _is_legacy_passed_manifest(payload):
        raise RuntimeError(
            f"Pytest run ownership mismatch: {candidate}"
        )
    if not isinstance(payload.get("finished_at"), str):
        raise RuntimeError(f"Passed pytest run has no finish time: {candidate}")
    return payload


def _is_legacy_passed_manifest(
    payload: dict[str, object],
) -> bool:
    return (
        "schema" not in payload
        and payload.get("status") == "passed"
        and payload.get("exit_status") == 0
        and payload.get("failed_nodes") == []
        and isinstance(payload.get("finished_at"), str)
        and type(payload.get("process_id")) is int
        and int(payload["process_id"]) > 0
        and type(payload.get("case_count")) is int
        and int(payload["case_count"]) >= 0
    )


def _remove_owned_passed_run(candidate: Path) -> None:
    _owned_passed_manifest(candidate)
    _remove_managed_directory(candidate)


@contextmanager
def _retention_lock() -> Iterator[None]:
    lock_path = MANAGED_PYTEST_ROOT / ".retention.lock"
    with lock_path.open("a+b") as handle:
        _lock_first_byte(handle)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _lock_first_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def pytest_configure(config: pytest.Config) -> None:
    MANAGED_PYTEST_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    compact_stamp = stamp[:15]
    run_root = (
        MANAGED_PYTEST_ROOT
        / f"r-{compact_stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    with _retention_lock():
        (run_root / "cases").mkdir(
            parents=True,
            exist_ok=False,
        )
        try:
            _write_manifest(
                run_root / "run-result.json",
                {
                    "schema": MANIFEST_SCHEMA,
                    "run_id": run_root.name,
                    "managed": True,
                    "status": "running",
                    "started_at": stamp,
                    "process_id": os.getpid(),
                    "failed_nodes": [],
                    "case_count": 0,
                    "cases": {},
                },
            )
        except BaseException as error:
            try:
                archive = (
                    MANAGED_PYTEST_ROOT
                    / "setup-failures"
                )
                archive.mkdir(exist_ok=True)
                destination = archive / run_root.name
                run_root.replace(destination)
                error.add_note(
                    "未完成的 pytest 运行目录已移到："
                    f"{destination}"
                )
            except BaseException as archive_error:
                error.add_note(
                    "pytest 运行清单写入失败，且未完成目录归档失败："
                    f"{archive_error}"
                )
            raise
    config.stash[RUN_STATE_KEY] = TestRunState(run_root)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        item.config.stash[RUN_STATE_KEY].failed_nodes.add(item.nodeid)
    if report.when == "teardown":
        item.config.stash[RUN_STATE_KEY].finished_nodes.add(item.nodeid)


def _run_manifest(
    state: TestRunState,
    *,
    status: str,
    exitstatus: int | pytest.ExitCode,
    finished_at: str,
    retained_nodes: set[str],
    retention_errors: list[dict[str, str]],
) -> dict[str, object]:
    failed_nodes = sorted(state.failed_nodes)
    retained = sorted(retained_nodes)
    return {
        "schema": MANIFEST_SCHEMA,
        "run_id": state.root.name,
        "managed": True,
        "status": status,
        "finished_at": finished_at,
        "process_id": os.getpid(),
        "exit_status": int(exitstatus),
        "failed_nodes": failed_nodes,
        "case_count": len(state.cases),
        "retained_nodes": retained,
        "retained_case_count": len(retained),
        "retention_status": (
            "complete" if not retention_errors else "incomplete"
        ),
        "retention_errors": retention_errors,
        "cases": {
            node_id: str(case_path.relative_to(state.root)).replace("\\", "/")
            for node_id, case_path in sorted(state.cases.items())
            if node_id in retained_nodes
        },
    }


def _finish_failed_run(
    state: TestRunState,
    exitstatus: int | pytest.ExitCode,
) -> None:
    retained_nodes = {
        node_id
        for node_id in state.cases
        if node_id in state.failed_nodes or node_id not in state.finished_nodes
    }
    retention_errors: list[dict[str, str]] = []
    for node_id, case_path in state.cases.items():
        if node_id in retained_nodes:
            continue
        error = _try_remove_managed_directory(
            case_path,
            node_id=node_id,
        )
        if error is not None:
            retained_nodes.add(node_id)
            retention_errors.append(error)
    _write_manifest(
        state.root / "run-result.json",
        _run_manifest(
            state,
            status="failed",
            exitstatus=exitstatus,
            finished_at=datetime.now(UTC).isoformat(),
            retained_nodes=retained_nodes,
            retention_errors=retention_errors,
        ),
    )


def _finish_passed_run(
    state: TestRunState,
    exitstatus: int | pytest.ExitCode,
) -> None:
    with _retention_lock():
        finished_at = datetime.now(UTC).isoformat()
        retained_nodes = set(state.cases)
        manifest = _run_manifest(
            state,
            status="passed",
            exitstatus=exitstatus,
            finished_at=finished_at,
            retained_nodes=retained_nodes,
            retention_errors=[],
        )
        manifest["retention_status"] = "pending"
        _write_manifest(state.root / "run-result.json", manifest)
        successful_runs: list[tuple[str, str, Path]] = []
        for candidate in MANAGED_PYTEST_ROOT.iterdir():
            if not candidate.is_dir():
                continue
            try:
                payload = _owned_passed_manifest(candidate)
            except RuntimeError:
                continue
            successful_runs.append(
                (str(payload["finished_at"]), candidate.name, candidate)
            )
        successful_runs.sort()
        retention_errors: list[dict[str, str]] = []
        for _, _, obsolete in successful_runs[:-1]:
            try:
                _remove_owned_passed_run(obsolete)
            except Exception as error:
                retention_errors.append(
                    _retention_error(obsolete, error)
                )
        manifest["retention_status"] = (
            "complete" if not retention_errors else "incomplete"
        )
        manifest["retention_errors"] = retention_errors
        _write_manifest(state.root / "run-result.json", manifest)


def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int | pytest.ExitCode,
) -> None:
    state = session.config.stash[RUN_STATE_KEY]
    if int(exitstatus) == int(pytest.ExitCode.OK):
        _finish_passed_run(state, exitstatus)
    else:
        _finish_failed_run(state, exitstatus)


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    """Keep the latest successful run and every failing case on D: for inspection."""
    state = request.config.stash[RUN_STATE_KEY]
    path = state.root / "cases" / _safe_case_name(request.node.nodeid)
    path.mkdir(parents=True, exist_ok=False)
    state.cases[request.node.nodeid] = path
    return path


@pytest.fixture
def max_project_path(tmp_path: Path) -> Path:
    """Return a project root at the maximum path accepted by the desktop UI."""

    path = safe_child_path(
        tmp_path,
        "Maximum-Project-Workspace-" * 20,
        max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT,
        max_component_utf16_units=PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    )
    assert utf16_units(str(path)) == PROJECT_ROOT_PATH_UTF16_LIMIT
    return path


@pytest.fixture(autouse=True)
def isolated_settings_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Prevent tests from adding fixture projects to the user's recent-project index."""
    path = tmp_path / "_settings" / "settings.json"
    monkeypatch.setenv("MEDIAFLOW_SETTINGS_PATH", str(path))
    monkeypatch.setenv("MEDIAFLOW_APP_ROOT", str(path.parent / "app"))
    return path
