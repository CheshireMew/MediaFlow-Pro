from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Literal

from mediaflow.atomic_file import atomic_write_text
from mediaflow.environment import test_run_root
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.storage_budget import (
    directory_inventory,
    require_test_artifact_budget,
)

MANIFEST_FILENAME = "run-result.json"
MANIFEST_SCHEMA = "mediaflow-script-run/v1"
VERIFICATION_WORK_ROOT_VARIABLE = "MEDIAFLOW_VERIFICATION_WORK_ROOT"

_CATEGORY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MANAGED_RUN_PATTERN = re.compile(
    r"^r-\d{8}T\d{6}\.\d{6}Z-\d+-[0-9a-f]{8}$"
)
_RunStatus = Literal["running", "passed", "failed"]
_LIFECYCLE_LOCK_TIMEOUT_SECONDS = 5.0
_LIFECYCLE_LOCK_POLL_SECONDS = 0.01


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _finished_at() -> str:
    return datetime.now(UTC).isoformat()


def verification_workspace_root(evidence_root: Path) -> Path:
    configured = os.environ.get(VERIFICATION_WORK_ROOT_VARIABLE, "").strip()
    if not configured:
        return evidence_root.expanduser().resolve()
    identity = hashlib.sha256(
        str(evidence_root.expanduser().resolve()).encode("utf-8")
    ).hexdigest()[:12]
    workspace = Path(configured).expanduser().resolve() / identity
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _validate_category(category: str) -> str:
    if not _CATEGORY_PATTERN.fullmatch(category):
        raise ValueError(
            "Verification run category must contain only lowercase letters, "
            "numbers, and single hyphens"
        )
    return category


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


@contextmanager
def _lifecycle_lock(category_root: Path) -> Iterator[None]:
    lock_path = category_root / ".lifecycle.lock"
    lock = ProcessFileLock(lock_path)
    deadline = time.monotonic() + _LIFECYCLE_LOCK_TIMEOUT_SECONDS
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Timed out waiting for verification run lifecycle lock: {lock_path}"
            )
        time.sleep(_LIFECYCLE_LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        lock.release()


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _read_manifest(path: Path) -> dict[str, object]:
    if _is_link(path):
        raise RuntimeError(f"Refusing to read a linked run manifest: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid verification run manifest: {path}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid verification run manifest object: {path}")
    return payload


def _owned_passed_manifest(
    category_root: Path,
    candidate: Path,
    category: str,
) -> dict[str, object]:
    root = category_root.resolve(strict=True)
    if _is_link(candidate):
        raise RuntimeError(f"Refusing to follow a linked verification run: {candidate}")
    resolved = candidate.resolve(strict=True)
    if (
        resolved == root
        or resolved.parent != root
        or not _MANAGED_RUN_PATTERN.fullmatch(candidate.name)
    ):
        raise RuntimeError(f"Refusing to inspect an unmanaged verification path: {resolved}")
    manifest = _read_manifest(candidate / MANIFEST_FILENAME)
    expected = {
        "schema": MANIFEST_SCHEMA,
        "category": category,
        "run_id": candidate.name,
        "managed": True,
        "status": "passed",
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"Verification run ownership mismatch: {candidate}")
    if not isinstance(manifest.get("finished_at"), str):
        raise RuntimeError(f"Passed verification run has no finish time: {candidate}")
    return manifest


def _remove_managed_run(
    category_root: Path,
    candidate: Path,
    category: str,
) -> None:
    _owned_passed_manifest(category_root, candidate, category)
    shutil.rmtree(candidate)


def _retain_latest_success(category_root: Path, category: str) -> None:
    successful: list[tuple[str, str, Path]] = []
    for candidate in category_root.iterdir():
        if not candidate.is_dir():
            continue
        try:
            manifest = _owned_passed_manifest(category_root, candidate, category)
        except RuntimeError:
            continue
        successful.append(
            (str(manifest["finished_at"]), candidate.name, candidate)
        )
    successful.sort()
    for _, _, obsolete in successful[:-1]:
        _remove_managed_run(category_root, obsolete, category)


def _archive_failed_run_creation(
    category_root: Path,
    path: Path,
) -> Path:
    archive = category_root / "setup-failures"
    archive.mkdir(exist_ok=True)
    destination = archive / path.name
    path.replace(destination)
    return destination


@dataclass(slots=True)
class VerificationRun:
    category: str
    path: Path
    managed: bool
    started_at: str
    category_root: Path | None = None
    storage_preflight: dict[str, object] = field(default_factory=dict)
    _finished: bool = field(default=False, init=False)
    _entered: bool = field(default=False, init=False)
    _previous_environment: dict[str, str | None] = field(
        default_factory=dict,
        init=False,
    )

    def __enter__(self) -> Path:
        if self._finished:
            raise RuntimeError(f"Verification run is already finished: {self.path}")
        if self._entered:
            raise RuntimeError(f"Verification run is already active: {self.path}")
        workspace = verification_workspace_root(self.path)
        isolated = {
            "MEDIAFLOW_SERVICE_SETTINGS_PATH": (
                workspace / "settings" / "service-settings.json"
            ),
            "MEDIAFLOW_DESKTOP_SETTINGS_PATH": (
                workspace / "settings" / "desktop-settings.json"
            ),
            "MEDIAFLOW_MEDIA_ROOT": workspace / "media",
            "MEDIAFLOW_PROJECT_ROOT": workspace / "projects",
            "MEDIAFLOW_SERVICE_STATE_DIR": workspace / "editor-service",
        }
        self._previous_environment = {
            name: os.environ.get(name) for name in isolated
        }
        for name, value in isolated.items():
            os.environ[name] = str(value)
        self._entered = True
        return self.path

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        exception_traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            if exception_type is None:
                self.mark_passed()
            else:
                self.mark_failed(exception_type, exception, exception_traceback)
        finally:
            self._restore_environment()
        return False

    def _restore_environment(self) -> None:
        for name, previous in self._previous_environment.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self._previous_environment.clear()
        self._entered = False

    def mark_passed(self) -> None:
        self._finish("passed")

    def mark_failed(
        self,
        exception_type: type[BaseException],
        exception: BaseException | None,
        exception_traceback: TracebackType | None,
    ) -> None:
        error = {
            "type": exception_type.__name__,
            "message": "" if exception is None else str(exception),
            "traceback": "".join(
                traceback.format_exception(
                    exception_type,
                    exception,
                    exception_traceback,
                )
            ),
        }
        self._finish("failed", error=error)

    def _finish(
        self,
        status: Literal["passed", "failed"],
        *,
        error: dict[str, str] | None = None,
    ) -> None:
        if self._finished:
            raise RuntimeError(f"Verification run is already finished: {self.path}")
        payload = self._manifest(status)
        if error is not None:
            payload["error"] = error
        if self.managed:
            if self.category_root is None:
                raise RuntimeError("Managed verification run has no category root")
            with _lifecycle_lock(self.category_root):
                _write_manifest(self.path / MANIFEST_FILENAME, payload)
                if status == "passed":
                    _retain_latest_success(self.category_root, self.category)
        else:
            _write_manifest(self.path / MANIFEST_FILENAME, payload)
        self._finished = True

    def _manifest(self, status: _RunStatus) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": MANIFEST_SCHEMA,
            "category": self.category,
            "run_id": self.path.name,
            "managed": self.managed,
            "status": status,
            "started_at": self.started_at,
            "process_id": os.getpid(),
        }
        if status != "running":
            payload["finished_at"] = _finished_at()
            payload["storage"] = {
                "preflight": self.storage_preflight,
                "final_inventory": directory_inventory(self.path),
                "cleanup": "report-only-until-authorized",
            }
        return payload


def verification_run(
    category: str,
    *,
    explicit_root: Path | None = None,
    explicit_parent: Path | None = None,
    managed_root: Path | None = None,
) -> VerificationRun:
    category = _validate_category(category)
    if explicit_root is not None and explicit_parent is not None:
        raise ValueError("Use either explicit_root or explicit_parent, not both")

    started_at = _timestamp()
    managed = explicit_root is None and explicit_parent is None
    category_root: Path | None = None
    if managed:
        selected_root = managed_root if managed_root is not None else test_run_root() / "scripts"
        requested_base = selected_root.expanduser().absolute()
        if requested_base.exists() and _is_link(requested_base):
            raise RuntimeError(
                f"Managed script run root cannot be linked: {requested_base}"
            )
        base = requested_base.resolve()
        storage_preflight = require_test_artifact_budget(
            base,
            expected_new_bytes=1024**3,
            label=f"MediaFlow {category} verification evidence",
        )
        base.mkdir(parents=True, exist_ok=True)
        category_root = base / category
        category_root.mkdir(exist_ok=True)
        if _is_link(category_root):
            raise RuntimeError(
                f"Managed script category root cannot be linked: {category_root}"
            )
        with _lifecycle_lock(category_root):
            run_id = f"r-{started_at}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            path = category_root / run_id
            path.mkdir(exist_ok=False)
    elif explicit_root is not None:
        path = explicit_root.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=False)
        storage_preflight = {}
    else:
        if explicit_parent is None:
            raise AssertionError("Explicit parent resolution is inconsistent")
        parent = explicit_parent.expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / (
            f"{category}-{started_at}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        path.mkdir(exist_ok=False)
        storage_preflight = {}

    run = VerificationRun(
        category=category,
        path=path,
        managed=managed,
        started_at=started_at,
        category_root=category_root,
        storage_preflight=storage_preflight,
    )
    try:
        _write_manifest(
            path / MANIFEST_FILENAME,
            run._manifest("running"),
        )
    except BaseException as error:
        if managed and category_root is not None:
            try:
                with _lifecycle_lock(category_root):
                    archived = _archive_failed_run_creation(
                        category_root,
                        path,
                    )
                error.add_note(
                    "未完成的验证运行目录已移到："
                    f"{archived}"
                )
            except BaseException as archive_error:
                error.add_note(
                    "验证运行清单写入失败，且未完成目录归档失败："
                    f"{archive_error}"
                )
        raise
    return run
