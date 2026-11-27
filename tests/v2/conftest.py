from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.storage_names import (
    PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    PROJECT_ROOT_PATH_UTF16_LIMIT,
    safe_child_path,
    utf16_units,
)
from mediaflow.environment import test_run_root
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.storage_budget import require_test_artifact_budget
from mediaflow.infrastructure.storage_inventory import run_payload_inventory
from mediaflow.infrastructure.storage_roots import managed_root_ownership, register_owned_storage_roots
from tests.pytest_artifacts import (
    CLEANUP,
    RetentionPolicy,
    checked_root,
    collect_runs,
    process_owner,
    remove_owned,
    trim_run_cases,
)

if TYPE_CHECKING:
    from tests.v2.editor_service_api import EditorServiceApi

os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
_windows_directory = os.environ.get("WINDIR", "").strip()
if _windows_directory:
    _windows_fonts = Path(_windows_directory) / "Fonts"
    if _windows_fonts.is_dir():
        os.environ.setdefault("QT_QPA_FONTDIR", str(_windows_fonts))


@pytest.fixture(scope="session")
def qapp_cls():
    """Use the same application base class as the QML-first desktop tests."""
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication


TEST_RUN_ROOT = test_run_root()
MANAGED_PYTEST_ROOT = TEST_RUN_ROOT / "p"
MANIFEST_SCHEMA = "mediaflow-pytest-run/v1"
RUN_STATE_KEY: pytest.StashKey[TestRunState] = pytest.StashKey()


@dataclass
class TestRunState:
    root: Path
    storage_preflight: dict[str, object] = field(default_factory=dict)
    cases: dict[str, Path] = field(default_factory=dict)
    failed_nodes: set[str] = field(default_factory=set)
    finished_nodes: set[str] = field(default_factory=set)
    owner: dict[str, int | float] = field(default_factory=process_owner)
    retention: RetentionPolicy = field(default_factory=RetentionPolicy.from_environment)
    cleanup_errors: list[dict[str, str]] = field(default_factory=list)
    startup_cleanup: dict[str, object] = field(default_factory=dict)


def _safe_case_name(node_id: str) -> str:
    # The node id remains in the manifest. Keeping it out of the directory name
    # leaves enough path budget for projects with deeply nested managed sources
    # on Windows.
    digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:12]
    return f"c-{digest}"


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


@contextmanager
def _retention_lock() -> Iterator[None]:
    lock_path = MANAGED_PYTEST_ROOT / ".retention.lock"
    lock = ProcessFileLock(lock_path)
    deadline = time.monotonic() + 60
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for pytest retention lock: {lock_path}")
        time.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def pytest_configure(config: pytest.Config) -> None:
    checked_root(MANAGED_PYTEST_ROOT)
    retention = RetentionPolicy.from_environment()
    MANAGED_PYTEST_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_identity = hashlib.sha256(f"{stamp}:{os.getpid()}:{uuid.uuid4().hex}".encode()).hexdigest()[:12]
    run_root = MANAGED_PYTEST_ROOT / f"r-{run_identity}"
    os.environ["MEDIAFLOW_STORAGE_REGISTRY_PATH"] = str(run_root / "root-registry.json")
    with _retention_lock():
        startup_cleanup = collect_runs(MANAGED_PYTEST_ROOT, retention)
        storage_preflight = require_test_artifact_budget(
            MANAGED_PYTEST_ROOT,
            expected_new_bytes=1024**3,
            label="MediaFlow pytest evidence",
        )
        (run_root / "c").mkdir(
            parents=True,
            exist_ok=False,
        )
        try:
            register_owned_storage_roots(
                (run_root,),
                ownership=managed_root_ownership(run_root),
                producer="pytest-and-verification-evidence",
                receipt=run_root / "run-result.json",
            )
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
                    "storage_preflight": storage_preflight,
                    "cleanup": CLEANUP,
                    "owner": process_owner(),
                },
            )
        except BaseException as error:
            try:
                archive = MANAGED_PYTEST_ROOT / "setup-failures"
                archive.mkdir(exist_ok=True)
                destination = archive / run_root.name
                run_root.replace(destination)
                error.add_note(f"未完成的 pytest 运行目录已移到：{destination}")
            except BaseException as archive_error:
                error.add_note(f"pytest 运行清单写入失败，且未完成目录归档失败：{archive_error}")
            raise
    config.stash[RUN_STATE_KEY] = TestRunState(
        run_root,
        storage_preflight=storage_preflight,
        retention=retention,
        startup_cleanup=startup_cleanup,
        cleanup_errors=list(startup_cleanup["errors"]),
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        item.config.stash[RUN_STATE_KEY].failed_nodes.add(item.nodeid)
    if report.when == "teardown":
        state = item.config.stash[RUN_STATE_KEY]
        state.finished_nodes.add(item.nodeid)
        case = state.cases.get(item.nodeid)
        if case is not None and item.nodeid not in state.failed_nodes:
            try:
                remove_owned(MANAGED_PYTEST_ROOT, state.root, case)
            except (OSError, ValueError) as error:
                state.cleanup_errors.append({"path": str(case), "error": str(error)})


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
        "owner": state.owner,
        "cleanup": CLEANUP,
        "exit_status": int(exitstatus),
        "failed_nodes": failed_nodes,
        "case_count": len(state.cases),
        "retained_nodes": retained,
        "retained_case_count": len(retained),
        "retention_status": ("complete" if not retention_errors else "incomplete"),
        "retention_errors": retention_errors,
        "storage": {
            "preflight": state.storage_preflight,
            "final_inventory": run_payload_inventory(state.root),
            "cleanup": CLEANUP,
            "startup_cleanup": state.startup_cleanup,
        },
        "cases": {
            node_id: str(case_path.relative_to(state.root)).replace("\\", "/")
            for node_id, case_path in sorted(state.cases.items())
            if node_id in retained_nodes
        },
    }


def _finish_run(state: TestRunState, exitstatus: int | pytest.ExitCode) -> None:
    with _retention_lock():
        for node in state.finished_nodes - state.failed_nodes:
            case = state.cases.get(node)
            if case is None or not case.exists():
                continue
            try:
                remove_owned(MANAGED_PYTEST_ROOT, state.root, case)
            except (OSError, ValueError) as error:
                state.cleanup_errors.append({"path": str(case), "error": str(error)})
        state.cleanup_errors.extend(trim_run_cases(MANAGED_PYTEST_ROOT, state.root, state.retention))
        retained = {node for node, path in state.cases.items() if path.exists()}
        status = (
            "passed" if int(exitstatus) == 0 else ("interrupted" if int(exitstatus) in {2, 3} else "failed")
        )
        _write_manifest(
            state.root / "run-result.json",
            _run_manifest(
                state,
                status=status,
                exitstatus=exitstatus,
                finished_at=datetime.now(UTC).isoformat(),
                retained_nodes=retained,
                retention_errors=state.cleanup_errors,
            ),
        )
        cleanup = collect_runs(MANAGED_PYTEST_ROOT, state.retention, exclude=state.root)
        state.cleanup_errors.extend(cleanup["errors"])
        if cleanup["errors"]:
            _write_manifest(
                state.root / "run-result.json",
                _run_manifest(
                    state,
                    status=status,
                    exitstatus=exitstatus,
                    finished_at=datetime.now(UTC).isoformat(),
                    retained_nodes=retained,
                    retention_errors=state.cleanup_errors,
                ),
            )
    if state.cleanup_errors:
        warnings.warn(
            pytest.PytestWarning(f"Test artifact cleanup was incomplete: {state.cleanup_errors}"),
            stacklevel=2,
        )


def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int | pytest.ExitCode,
) -> None:
    if RUN_STATE_KEY in session.config.stash:
        _finish_run(session.config.stash[RUN_STATE_KEY], exitstatus)


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    """Keep managed test evidence under the configured, budgeted test root."""
    state = request.config.stash[RUN_STATE_KEY]
    path = state.root / "c" / _safe_case_name(request.node.nodeid)
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
def isolated_storage_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Keep settings, media, and automatic project creation inside the test case."""
    path = tmp_path / "_settings" / "service-settings.json"
    desktop_path = path.with_name("desktop-settings.json")
    monkeypatch.setenv("MEDIAFLOW_SERVICE_SETTINGS_PATH", str(path))
    monkeypatch.setenv("MEDIAFLOW_DESKTOP_SETTINGS_PATH", str(desktop_path))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(path.parent / "media"))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "_projects"))
    monkeypatch.setenv("MEDIAFLOW_SERVICE_STATE_DIR", str(tmp_path / "_service"))
    monkeypatch.setenv("MEDIAFLOW_STORAGE_REGISTRY_PATH", str(tmp_path / "_settings" / "root-registry.json"))
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_MAX_BYTES", str(8 * 1024**3))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ARTIFACT_MAX_BYTES", str(8 * 1024**3))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ARTIFACTS_MAX_BYTES", str(16 * 1024**3))
    monkeypatch.setenv("MEDIAFLOW_DOWNLOAD_OPERATION_MAX_BYTES", str(4 * 1024**3))
    monkeypatch.setenv("MEDIAFLOW_DELIVERY_OPERATION_MAX_BYTES", str(8 * 1024**3))
    try:
        yield path
    finally:
        from mediaflow.service.client import shutdown_sync_service

        shutdown_sync_service()


@pytest.fixture
def editor_service_api() -> Iterator[EditorServiceApi]:
    from tests.v2.editor_service_api import EditorServiceApi

    api = EditorServiceApi()
    try:
        yield api
    finally:
        api.shutdown()
