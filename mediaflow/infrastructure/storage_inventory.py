from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.storage_allocation import FileAllocation, allocation_unit, file_allocation
from mediaflow.infrastructure.storage_policy import (
    PROJECT_CACHE_OWNER_FILENAME,
    PROJECT_CACHE_OWNER_LOCK_TIMEOUT_SECONDS,
)


def _terminal_manifest(path: Path) -> str | None:
    manifest = path / "run-result.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    status = payload.get("status") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("schema") not in {
        "mediaflow-pytest-run/v1",
        "mediaflow-script-run/v1",
    }:
        return None
    return status if status in {"passed", "failed", "interrupted"} else None


class StorageInventoryError(RuntimeError):
    """An incomplete measurement must never be interpreted as available capacity."""


def _scan_files(
    root: Path, *, check_cancelled: Callable[[], None] | None = None,
) -> tuple[dict[str, FileAllocation], list[str], dict[str, str]]:
    try:
        if not root.is_dir():
            if root.exists():
                raise NotADirectoryError(str(root))
            return {}, [], {}
    except OSError as error:
        raise StorageInventoryError(f"Storage inventory incomplete at {root}: {error}") from error
    files, linked, terminals, children = _scan_directories(
        [(str(root), "")], check_cancelled=check_cancelled, shallow=True,
    )
    if len(children) < 2:
        remaining = [_scan_directories(children, check_cancelled=check_cancelled)]
    else:
        stopped = threading.Event()

        def check_stopped() -> None:
            if stopped.is_set():
                raise InterruptedError("Storage inventory interrupted")

        # A fixed number of subtree batches bounds both the thread count and
        # queued work, even when a cache has thousands of project directories.
        worker_count = min(8, len(children))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="storage-inventory") as pool:
            pending = {
                pool.submit(_scan_directories, children[index::worker_count], check_cancelled=check_stopped)
                for index in range(worker_count)
            }
            remaining = []
            try:
                while pending:
                    if check_cancelled is not None:
                        check_cancelled()
                    completed, pending = wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
                    remaining.extend(future.result() for future in completed)
            finally:
                stopped.set()
    for subtree_files, subtree_links, subtree_terminals, _children in remaining:
        files.update(subtree_files)
        linked.extend(subtree_links)
        terminals.update(subtree_terminals)
    return files, linked, terminals


def _scan_directories(
    stack: list[tuple[str, str]], *,
    check_cancelled: Callable[[], None] | None = None,
    shallow: bool = False,
) -> tuple[dict[str, FileAllocation], list[str], dict[str, str], list[tuple[str, str]]]:
    files: dict[str, FileAllocation] = {}
    linked: list[str] = []
    terminals: dict[str, str] = {}
    units: dict[int, int] = {}
    current = ""
    try:
        while stack:
            if check_cancelled is not None:
                check_cancelled()
            current, relative_root = stack.pop()
            try:
                with os.scandir(current) as stream:
                    entries = list(stream)
            except FileNotFoundError:
                continue
            for index, entry in enumerate(entries):
                if check_cancelled is not None and index % 64 == 0:
                    check_cancelled()
                relative = f"{relative_root}/{entry.name}" if relative_root else entry.name
                try:
                    if entry.is_symlink() or (os.name == "nt" and entry.is_junction()):
                        linked.append(relative)
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, relative))
                    elif entry.is_file(follow_symlinks=False):
                        # Windows DirEntry.stat omits the stable object/link
                        # identity; obtain it from the actual filesystem stat.
                        stat = os.stat(entry.path, follow_symlinks=False)
                        if stat.st_dev not in units:
                            units[stat.st_dev] = allocation_unit(entry.path)
                        files[relative] = file_allocation(
                            entry.path, stat, allocation_unit_bytes=units[stat.st_dev]
                        )
                        if relative_root and entry.name == "run-result.json":
                            status = _terminal_manifest(Path(current))
                            if status is not None:
                                terminals[relative_root] = status
                except FileNotFoundError:
                    # A producer can atomically replace or remove an observed entry.
                    # Other I/O and identity errors remain failures, not zero bytes.
                    continue
            if shallow:
                break
    except OSError as error:
        raise StorageInventoryError(f"Storage inventory incomplete at {current}: {error}") from error
    return files, linked, terminals, stack


def _file_metrics(
    files: dict[str, FileAllocation], *, check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    objects: dict[tuple[int, int], FileAllocation] = {}
    for index, value in enumerate(files.values()):
        if check_cancelled is not None and index % 256 == 0:
            check_cancelled()
        previous = objects.get(value.identity)
        if previous is None or value.allocated_bytes > previous.allocated_bytes:
            objects[value.identity] = value
    return {
        "logical_bytes": sum(value.logical_bytes for value in files.values()),
        "allocated_bytes": sum(value.allocated_bytes for value in objects.values()),
        "unique_object_bytes": sum(value.logical_bytes for value in objects.values()),
        "files": len(files),
        "unique_objects": len(objects),
        "has_hard_links": any(value.links > 1 for value in objects.values()),
        "object_identity_sha256": hashlib.sha256(
            json.dumps(
                sorted((key, value.logical_bytes, value.allocated_bytes) for key, value in objects.items())
            ).encode()
        ).hexdigest(),
    }


def file_inventory(path: str | Path) -> dict[str, Any]:
    selected = Path(path).expanduser().resolve()
    return {"root": str(selected), **_file_metrics({selected.name: file_allocation(selected)})}


def _directory_report(
    root: Path,
    files: dict[str, FileAllocation],
    linked: list[str],
    terminals: dict[str, str],
    *, check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    by_category: dict[str, dict[str, FileAllocation]] = {}
    for index, (relative, value) in enumerate(files.items()):
        if check_cancelled is not None and index % 256 == 0:
            check_cancelled()
        by_category.setdefault(relative.split("/", 1)[0], {})[relative] = value
    terminal_roots: list[tuple[str, str]] = []
    for relative, status in sorted(terminals.items(), key=lambda item: (item[0].count("/"), item[0])):
        if not any(relative.startswith(parent + "/") for parent, _ in terminal_roots):
            terminal_roots.append((relative, status))
    candidates = []
    for relative, status in terminal_roots:
        if check_cancelled is not None:
            check_cancelled()
        owned = {name: value for name, value in files.items() if name.startswith(relative + "/")}
        owned_links: dict[tuple[int, int], list[FileAllocation]] = {}
        for value in owned.values():
            owned_links.setdefault(value.identity, []).append(value)
        exclusive_upper = sum(
            max(value.allocated_bytes for value in values)
            for values in owned_links.values()
            if len(values) == values[0].links
        )
        candidates.append(
            {
                "path": relative,
                "status": status,
                **_file_metrics(owned),
                "exclusive_reclaimable_allocated_bytes": {"lower_bound": 0, "upper_bound": exclusive_upper},
                "reason": "terminal managed run; references and sharing require review before cleanup",
            }
        )
    return {
        "schema": "mediaflow-storage-inventory/v2",
        "root": str(root),
        **_file_metrics(files, check_cancelled=check_cancelled),
        "measurement": "filesystem-file-allocation-upper-bound",
        "measurement_limits": (
            "Shared extents may be counted conservatively; "
            "filesystem-wide metadata is not attributed to individual files."
        ),
        "categories": {
            key: _file_metrics(value, check_cancelled=check_cancelled)
            for key, value in sorted(by_category.items())
        },
        "linked_paths_not_followed": sorted(linked),
        "cleanup_candidates": candidates,
        "cleanup": "report-only-until-authorized",
    }


def directory_inventory(
    root: str | Path, *, check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    selected = Path(root).expanduser().resolve()
    return _directory_report(
        selected, *_scan_files(selected, check_cancelled=check_cancelled), check_cancelled=check_cancelled,
    )


def run_payload_inventory(
    root: str | Path, *, excluded_control_files: tuple[str, ...] = ("run-result.json",)
) -> dict[str, Any]:
    """A terminal manifest describes its payload, not its own changing JSON size."""
    selected = Path(root).expanduser().resolve()
    files, linked, terminals = _scan_files(selected)
    for name in excluded_control_files:
        files.pop(name, None)
    return {
        **_directory_report(selected, files, linked, terminals),
        "excluded_control_files": list(excluded_control_files),
    }


def registered_roots_inventory(
    roots: dict[str, Path], *, check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Measure every registered root and deduplicate the same physical objects."""
    all_files: dict[str, FileAllocation] = {}
    linked: list[str]
    terminals: dict[str, str]
    inventories: dict[str, Any] = {}
    scanned: dict[Path, tuple[dict[str, FileAllocation], list[str], dict[str, str]]] = {}
    for name, path in sorted(roots.items(), key=lambda item: (len(item[1].parts), str(item[1]))):
        if check_cancelled is not None:
            check_cancelled()
        selected = path.expanduser().resolve()
        is_file = selected.is_file()
        parent = next((path for path in scanned if selected.is_relative_to(path)), None)
        if is_file:
            files, linked, terminals = {selected.name: file_allocation(selected)}, [], {}
        elif parent is not None:
            prefix = selected.relative_to(parent).as_posix()
            prefix = "" if prefix == "." else prefix + "/"
            previous_files, previous_linked, previous_terminals = scanned[parent]
            files = {
                key[len(prefix) :]: value for key, value in previous_files.items() if key.startswith(prefix)
            }
            linked = [key[len(prefix) :] for key in previous_linked if key.startswith(prefix)]
            terminals = {
                key[len(prefix) :]: value
                for key, value in previous_terminals.items()
                if key.startswith(prefix)
            }
        else:
            files, linked, terminals = _scan_files(selected, check_cancelled=check_cancelled)
            scanned[selected] = files, linked, terminals
        inventories[name] = _directory_report(
            selected, files, linked, terminals, check_cancelled=check_cancelled,
        )
        for index, (relative, value) in enumerate(files.items()):
            if check_cancelled is not None and index % 256 == 0:
                check_cancelled()
            all_files[os.path.normcase(str(selected if is_file else selected / relative))] = value
    return {
        "roots": inventories,
        "total": _file_metrics(all_files, check_cancelled=check_cancelled),
        "cleanup": "report-only-until-authorized",
    }


def project_cache_identity(
    project_dir: str | Path,
    *,
    case_sensitive_paths: bool,
) -> str:
    normalized = str(Path(project_dir).expanduser().resolve())
    if not case_sensitive_paths:
        normalized = normalized.casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def register_project_cache_owner(
    cache_root: str | Path,
    project_dir: str | Path,
    *,
    case_sensitive_paths: bool,
) -> dict[str, object]:
    root = Path(cache_root).expanduser().resolve()
    project = Path(project_dir).expanduser().resolve()
    identity = project_cache_identity(
        project,
        case_sensitive_paths=case_sensitive_paths,
    )
    if root.name.casefold() != identity:
        raise RuntimeError(f"Project cache root identity does not match its project path: {root}")
    payload: dict[str, object] = {
        "schema": "mediaflow-project-cache-owner/v1",
        "project_path": str(project),
        "project_identity": identity,
    }
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / PROJECT_CACHE_OWNER_FILENAME
    lock = ProcessFileLock(manifest.with_suffix(f"{manifest.suffix}.lock"))
    if not lock.acquire_until(timeout_seconds=PROJECT_CACHE_OWNER_LOCK_TIMEOUT_SECONDS):
        raise RuntimeError(f"Timed out registering project cache owner: {manifest}")
    try:
        if manifest.is_file():
            try:
                existing = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError(f"Invalid project cache owner manifest: {manifest}") from error
            if existing != payload:
                raise RuntimeError(f"Project cache owner mismatch: {manifest}")
            return payload
        atomic_write_text(
            manifest,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
    finally:
        lock.release()
    return payload


def project_cache_inventory(root: str | Path, *, retained_roots: set[Path] | None = None) -> dict[str, Any]:
    selected = Path(root).expanduser().resolve()
    aggregate = directory_inventory(selected)
    projects: list[dict[str, Any]] = []
    project_cleanup_candidates: list[dict[str, Any]] = []
    if selected.is_dir():
        for candidate in sorted(selected.iterdir(), key=lambda item: item.name.casefold()):
            if (
                not candidate.is_dir()
                or candidate.is_symlink()
                or (os.name == "nt" and candidate.is_junction())
            ):
                continue
            inventory = directory_inventory(candidate)
            manifest = candidate / PROJECT_CACHE_OWNER_FILENAME
            owner: dict[str, object] | None = None
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if (
                    isinstance(payload, dict)
                    and payload.get("schema") == "mediaflow-project-cache-owner/v1"
                    and isinstance(payload.get("project_path"), str)
                ):
                    owner = payload
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
            project_path = None if owner is None else str(owner["project_path"])
            project_exists = None if project_path is None else Path(project_path).exists()
            record = {
                "cache_key": candidate.name,
                "project_path": project_path,
                "project_exists": project_exists,
                "owner_status": "known" if owner is not None else "unknown-review-required",
                "producer_status": (
                    "unknown"
                    if retained_roots is None
                    else "retained"
                    if candidate.resolve() in retained_roots
                    else "settled"
                ),
                "logical_bytes": inventory["logical_bytes"],
                "allocated_bytes": inventory["allocated_bytes"],
                "unique_object_bytes": inventory["unique_object_bytes"],
                "files": inventory["files"],
            }
            projects.append(record)
            if (
                project_path is not None
                and project_exists is False
                and retained_roots is not None
                and candidate.resolve() not in retained_roots
            ):
                project_cleanup_candidates.append(
                    {
                        "path": candidate.name,
                        "project_path": project_path,
                        "logical_bytes": inventory["logical_bytes"],
                        "allocated_bytes": inventory["allocated_bytes"],
                        "unique_object_bytes": inventory["unique_object_bytes"],
                        "exclusive_reclaimable_allocated_bytes": {
                            "lower_bound": 0,
                            "upper_bound": inventory["allocated_bytes"],
                        },
                        "reason": "owned derived cache whose source project no longer exists",
                    }
                )
    aggregate["projects"] = projects
    aggregate["cleanup_candidates"] = [
        *aggregate["cleanup_candidates"],
        *sorted(project_cleanup_candidates, key=lambda item: item["allocated_bytes"], reverse=True),
    ]
    return aggregate
