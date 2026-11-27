"""Bounded, explicitly authorized retention of registered pytest artifacts."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.storage_names import canonical_resolved_path, python_io_path

SCHEMA = "mediaflow-pytest-run/v1"
CLEANUP = "automatic-registered-test-artifacts"
_RUN_NAME = re.compile(r"r-[0-9a-f]{12}\Z")


@dataclass(frozen=True)
class RetentionPolicy:
    passed_runs: int = 10
    failed_runs: int = 3
    failed_days: int = 7
    failed_bytes: int = 1024**3

    @classmethod
    def from_environment(cls) -> RetentionPolicy:
        values = {}
        for field, variable in (
            ("passed_runs", "MEDIAFLOW_TEST_KEEP_PASSED_RUNS"),
            ("failed_runs", "MEDIAFLOW_TEST_KEEP_FAILED_RUNS"),
            ("failed_days", "MEDIAFLOW_TEST_FAILED_DAYS"),
            ("failed_bytes", "MEDIAFLOW_TEST_FAILED_MAX_BYTES"),
        ):
            raw = os.environ.get(variable)
            if raw is None:
                continue
            try:
                value = int(raw)
            except ValueError as error:
                raise ValueError(f"{variable} must be a nonnegative integer") from error
            if value < 0:
                raise ValueError(f"{variable} must be a nonnegative integer")
            values[field] = value
        return cls(**values)


def process_owner() -> dict[str, int | float]:
    return {"pid": os.getpid(), "process_started": psutil.Process().create_time()}


def _linked(path: Path) -> bool:
    return path.is_symlink() or (os.name == "nt" and path.is_junction())


def checked_root(root: Path) -> Path:
    absolute = root.absolute()
    if absolute.name != "p" or absolute == Path(absolute.anchor):
        raise ValueError(f"Invalid managed pytest root: {root}")
    for ancestor in (absolute, *absolute.parents):
        if _linked(ancestor):
            raise ValueError(f"Managed pytest root contains a directory link: {ancestor}")
    return absolute.resolve()


def registered_run(root: Path, run: Path) -> dict | None:
    if run.parent != root or not _RUN_NAME.fullmatch(run.name) or _linked(run):
        return None
    manifest = run / "run-result.json"
    try:
        if _linked(manifest) or manifest.stat().st_size > 8 * 1024**2:
            return None
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict) or (
        payload.get("schema") != SCHEMA
        or payload.get("managed") is not True
        or payload.get("run_id") != run.name
        or payload.get("status") not in {"running", "passed", "failed", "interrupted"}
    ):
        return None
    return payload


def owner_active(payload: dict) -> bool:
    owner = payload.get("owner", {})
    pid = owner.get("pid") if isinstance(owner, dict) else None
    pid = payload.get("process_id") if pid is None else pid
    if type(pid) is not int or pid <= 0:
        return payload.get("status") == "running"
    try:
        process = psutil.Process(pid)
        if not process.is_running():
            return False
        started = owner.get("process_started") if isinstance(owner, dict) else None
        # Legacy manifests have no process creation time; conservatively protect a live PID.
        if started is not None:
            return abs(process.create_time() - float(started)) < 0.001
        stamp = payload.get("finished_at") or payload.get("started_at")
        if isinstance(stamp, str):
            recorded = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=UTC)
            if process.create_time() > recorded.timestamp() + 1:
                return False
        return True
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError, TypeError, ValueError):
        return True


def artifact_bytes(path: Path) -> int:
    total = 0
    stack = [path]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                child = Path(entry.path)
                if _linked(child):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(child)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


def remove_owned(root: Path, run: Path, target: Path) -> None:
    root = checked_root(root)
    run = run.absolute()
    target = target.absolute()
    if registered_run(root, run) is None:
        raise ValueError(f"Refusing cleanup of an unregistered test run: {run}")
    if target != run and not (
        target.parent == run / "c" and re.fullmatch(r"c-[0-9a-f]{12}", target.name)
    ):
        raise ValueError(f"Refusing cleanup outside a registered test case: {target}")
    if target != run and _linked(run / "c"):
        raise ValueError(f"Test case container is a directory link: {run / 'c'}")
    if _linked(target) or not target.resolve().is_relative_to(root):
        raise ValueError(f"Refusing cleanup through a directory link: {target}")
    # Python >=3.12 rmtree unlinks child junctions/symlinks without visiting their targets.
    # Only the verified run or one of its direct case directories can reach this operation.
    def remove_readonly(function, path, error):
        item = Path(path)
        info = item.lstat()
        if not isinstance(error, PermissionError) or _linked(item) or not (
            info.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        ):
            raise error
        if not canonical_resolved_path(item).is_relative_to(canonical_resolved_path(target)):
            raise ValueError(f"Readonly cleanup path escaped its test directory: {item}")
        item.chmod(info.st_mode | stat.S_IWRITE)
        function(path)

    shutil.rmtree(python_io_path(target), onexc=remove_readonly)


def trim_run_cases(root: Path, run: Path, policy: RetentionPolicy) -> list[dict[str, str]]:
    """Bound a single failed run, including a large failure generated by this process."""
    errors = []
    container = run / "c"
    if not container.is_dir() or _linked(container):
        return errors
    cases = []
    for case in container.iterdir():
        if not case.is_dir() or _linked(case) or not re.fullmatch(r"c-[0-9a-f]{12}", case.name):
            continue
        cases.append((artifact_bytes(case), case))
    total = sum(size for size, _ in cases)
    for size, case in sorted(cases, key=lambda item: item[0], reverse=True):
        if total <= policy.failed_bytes:
            break
        try:
            remove_owned(root, run, case)
            total -= size
        except (OSError, ValueError) as error:
            errors.append({"path": str(case), "error": str(error)})
    return errors


def collect_runs(root: Path, policy: RetentionPolicy, *, exclude: Path | None = None) -> dict:
    """Called under the conftest retention lock, before admission and after completion."""
    root = checked_root(root)
    result = {"cleanup": CLEANUP, "removed": [], "errors": [], "active": [], "unknown": []}
    if not root.exists():
        return result
    records = []
    for run in root.iterdir():
        if run == exclude or not run.is_dir():
            continue
        payload = registered_run(root, run)
        if payload is None:
            result["unknown"].append(str(run))
            continue
        if owner_active(payload):
            result["active"].append(str(run))
            continue
        try:
            if payload["status"] in {"passed", "failed"}:
                cases = payload.get("cases", {})
                failed_nodes = set(payload.get("failed_nodes", []))
                if not isinstance(cases, dict):
                    raise ValueError("Invalid registered case mapping")
                for node_id, relative in list(cases.items()):
                    if node_id in failed_nodes:
                        continue
                    if not isinstance(relative, str) or not re.fullmatch(r"c/c-[0-9a-f]{12}", relative):
                        raise ValueError("Invalid registered case path")
                    case = run / relative
                    if case.exists():
                        remove_owned(root, run, case)
                    del cases[node_id]
                payload["cases"] = cases
                payload["retained_nodes"] = sorted(cases)
                payload["retained_case_count"] = len(cases)
                payload["cleanup"] = CLEANUP
                atomic_write_text(run / "run-result.json", json.dumps(payload, ensure_ascii=False))
            stamp = payload.get("finished_at") or payload.get("started_at")
            finished = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else (
                datetime.fromtimestamp(run.stat().st_mtime, UTC)
            )
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=UTC)
            size = artifact_bytes(run)
            records.append((finished, run, payload, size))
        except (OSError, ValueError, TypeError, AttributeError) as error:
            result["errors"].append({"path": str(run), "error": str(error)})
    passed = failed = retained_bytes = 0
    if exclude is not None:
        own = registered_run(root, exclude)
        if own is not None:
            if own["status"] == "passed":
                passed = 1
            else:
                failed = 1
                retained_bytes = artifact_bytes(exclude)
    cutoff = datetime.now(UTC) - timedelta(days=policy.failed_days)
    for finished, run, payload, size in sorted(records, key=lambda item: item[0], reverse=True):
        if payload["status"] == "passed":
            passed += 1
            should_remove = passed > policy.passed_runs
        else:
            failed += 1
            should_remove = (
                failed > policy.failed_runs or finished < cutoff
                or retained_bytes + size > policy.failed_bytes
            )
            if not should_remove:
                retained_bytes += size
        if not should_remove:
            continue
        try:
            # Re-read ownership immediately before acting; another process may have adopted it.
            current = registered_run(root, run)
            if current is None or owner_active(current):
                continue
            remove_owned(root, run, run)
            result["removed"].append({"path": str(run), "bytes": size})
        except (OSError, ValueError) as error:
            result["errors"].append({"path": str(run), "error": str(error)})
    return result
