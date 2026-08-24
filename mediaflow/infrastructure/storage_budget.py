from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure.project_lock import ProcessFileLock

GIB = 1024**3
MIB = 1024**2
PROJECT_CACHE_OWNER_FILENAME = ".mediaflow-storage-owner.json"
PROJECT_CACHE_OWNER_LOCK_TIMEOUT_SECONDS = 15.0
PROJECT_CACHE_RESERVATION_LEDGER_SCHEMA = "mediaflow-project-cache-reservations/v2"
PROJECT_CACHE_RESERVATION_LEDGER_FILENAME = ".mediaflow-project-cache-reservations.json"
PROJECT_CACHE_DEFERRED_RESERVATION_MAX_BYTES = 64 * MIB
PROJECT_CACHE_DEFERRED_RESERVATION_LIMIT_DIVISOR = 1024
PROJECT_CACHE_MAX_BYTES_VARIABLE = "MEDIAFLOW_PROJECT_CACHE_MAX_BYTES"
PROJECT_CACHES_MAX_BYTES_VARIABLE = "MEDIAFLOW_PROJECT_CACHES_MAX_BYTES"
TEST_ARTIFACT_MAX_BYTES_VARIABLE = "MEDIAFLOW_TEST_ARTIFACT_MAX_BYTES"
QUALITY_RUN_MAX_BYTES_VARIABLE = "MEDIAFLOW_QUALITY_RUN_MAX_BYTES"
RUNTIME_MAX_BYTES_VARIABLE = "MEDIAFLOW_RUNTIME_MAX_BYTES"
PROJECT_ARTIFACT_MAX_BYTES_VARIABLE = "MEDIAFLOW_PROJECT_ARTIFACT_MAX_BYTES"
PROJECT_ARTIFACTS_MAX_BYTES_VARIABLE = "MEDIAFLOW_PROJECT_ARTIFACTS_MAX_BYTES"
DOWNLOAD_OPERATION_MAX_BYTES_VARIABLE = "MEDIAFLOW_DOWNLOAD_OPERATION_MAX_BYTES"
DELIVERY_OPERATION_MAX_BYTES_VARIABLE = "MEDIAFLOW_DELIVERY_OPERATION_MAX_BYTES"
MINIMUM_FREE_BYTES_VARIABLE = "MEDIAFLOW_MINIMUM_FREE_BYTES"
STORAGE_RECEIPT_SCHEMA = "mediaflow-storage-receipt/v1"
STORAGE_RECEIPT_DIRECTORY = "storage-receipts"
PROXY_VIDEO_MAX_BITRATE = 12_000_000
PROXY_AUDIO_BITRATE = 128_000


@dataclass(frozen=True, slots=True)
class StoragePolicy:
    project_cache_max_bytes: int = 32 * GIB
    project_caches_max_bytes: int = 64 * GIB
    test_artifact_max_bytes: int = 32 * GIB
    quality_run_max_bytes: int = 16 * GIB
    runtime_max_bytes: int = 96 * GIB
    project_artifact_max_bytes: int = 64 * GIB
    project_artifacts_max_bytes: int = 128 * GIB
    download_operation_max_bytes: int = 64 * GIB
    delivery_operation_max_bytes: int = 256 * GIB
    minimum_free_bytes: int = 80 * GIB


def _positive_environment_integer(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer byte count") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer byte count")
    return value


def load_storage_policy() -> StoragePolicy:
    defaults = StoragePolicy()
    return StoragePolicy(
        project_cache_max_bytes=_positive_environment_integer(
            PROJECT_CACHE_MAX_BYTES_VARIABLE,
            defaults.project_cache_max_bytes,
        ),
        project_caches_max_bytes=_positive_environment_integer(
            PROJECT_CACHES_MAX_BYTES_VARIABLE,
            defaults.project_caches_max_bytes,
        ),
        test_artifact_max_bytes=_positive_environment_integer(
            TEST_ARTIFACT_MAX_BYTES_VARIABLE,
            defaults.test_artifact_max_bytes,
        ),
        quality_run_max_bytes=_positive_environment_integer(
            QUALITY_RUN_MAX_BYTES_VARIABLE,
            defaults.quality_run_max_bytes,
        ),
        runtime_max_bytes=_positive_environment_integer(
            RUNTIME_MAX_BYTES_VARIABLE,
            defaults.runtime_max_bytes,
        ),
        project_artifact_max_bytes=_positive_environment_integer(
            PROJECT_ARTIFACT_MAX_BYTES_VARIABLE,
            defaults.project_artifact_max_bytes,
        ),
        project_artifacts_max_bytes=_positive_environment_integer(
            PROJECT_ARTIFACTS_MAX_BYTES_VARIABLE,
            defaults.project_artifacts_max_bytes,
        ),
        download_operation_max_bytes=_positive_environment_integer(
            DOWNLOAD_OPERATION_MAX_BYTES_VARIABLE,
            defaults.download_operation_max_bytes,
        ),
        delivery_operation_max_bytes=_positive_environment_integer(
            DELIVERY_OPERATION_MAX_BYTES_VARIABLE,
            defaults.delivery_operation_max_bytes,
        ),
        minimum_free_bytes=_positive_environment_integer(
            MINIMUM_FREE_BYTES_VARIABLE,
            defaults.minimum_free_bytes,
        ),
    )


def estimate_video_cache_bytes(
    width: int,
    height: int,
    frame_count: int,
    *,
    concurrent_copies: int = 2,
) -> int:
    values = (width, height, frame_count, concurrent_copies)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("Video cache estimates require positive integer dimensions and counts")
    return max(64 * 1024**2, width * height * frame_count * concurrent_copies)


def estimate_proxy_peak_bytes(
    duration_seconds: float,
    *,
    output_count: int = 1,
) -> int | None:
    if duration_seconds <= 0 or output_count <= 0:
        return None
    encoded = duration_seconds * (PROXY_VIDEO_MAX_BITRATE + PROXY_AUDIO_BITRATE) / 8
    container_headroom = encoded * 1.15
    return max(64 * 1024**2, int(container_headroom * output_count))


def estimate_download_peak_bytes(
    duration_seconds: float,
    *,
    media_kind: str,
    resolution: str,
) -> int | None:
    if duration_seconds <= 0:
        return None
    if media_kind == "audio" or resolution == "audio":
        payload_bitrate = 512_000
    else:
        normalized = resolution.strip().lower()
        height_rates = {
            2160: 120_000_000,
            1440: 60_000_000,
            1080: 30_000_000,
            720: 15_000_000,
            480: 8_000_000,
            360: 5_000_000,
        }
        if normalized == "best":
            payload_bitrate = height_rates[2160]
        else:
            aliases = {"4k": 2160, "2k": 1440}
            try:
                height = (
                    aliases[normalized]
                    if normalized in aliases
                    else int(normalized.removesuffix("p"))
                )
            except ValueError:
                return None
            selected_height = min(height_rates, key=lambda item: abs(item - height))
            payload_bitrate = height_rates[selected_height]
        payload_bitrate += 512_000
    final_payload = duration_seconds * payload_bitrate / 8
    # Separate streams, merged output, subtitles and failure archival can coexist.
    return max(64 * 1024**2, int(final_payload * 4 + 64 * 1024**2))


def estimate_pcm_audio_peak_bytes(
    duration_seconds: float,
    *,
    sample_rate: int,
    channels: int = 1,
    concurrent_copies: int = 2,
) -> int | None:
    if (
        duration_seconds <= 0
        or sample_rate <= 0
        or channels <= 0
        or concurrent_copies <= 0
    ):
        return None
    return max(
        16 * 1024**2,
        int(duration_seconds * sample_rate * channels * 2 * concurrent_copies),
    )


def _terminal_manifest(path: Path) -> str | None:
    manifest = path / "run-result.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    status = payload.get("status") if isinstance(payload, dict) else None
    return status if status in {"passed", "failed", "interrupted"} else None


def directory_inventory(root: str | Path) -> dict[str, Any]:
    selected = Path(root).expanduser().resolve()
    categories: dict[str, dict[str, int]] = {}
    linked_paths: list[str] = []
    terminal_roots: dict[str, str] = {}
    directory_bytes = {"": 0}
    directory_files = {"": 0}
    total_bytes = 0
    total_files = 0
    if selected.is_dir():
        stack = [(str(selected), "")]
        while stack:
            current, current_relative = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            if current_relative:
                for entry in entries:
                    try:
                        is_terminal_manifest = (
                            entry.name == "run-result.json"
                            and not entry.is_symlink()
                            and entry.is_file(follow_symlinks=False)
                        )
                    except OSError:
                        continue
                    if is_terminal_manifest:
                        status = _terminal_manifest(Path(current))
                        if status is not None:
                            terminal_roots[current_relative] = status
                        break
            for entry in entries:
                relative = (
                    f"{current_relative}/{entry.name}"
                    if current_relative
                    else entry.name
                )
                top = relative.split("/", 1)[0]
                try:
                    if entry.is_symlink():
                        linked_paths.append(relative)
                    elif entry.is_dir(follow_symlinks=False):
                        directory_bytes[relative] = 0
                        directory_files[relative] = 0
                        stack.append((entry.path, relative))
                    elif entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        total_bytes += size
                        total_files += 1
                        directory_bytes[current_relative] += size
                        directory_files[current_relative] += 1
                        category = categories.setdefault(top, {"bytes": 0, "files": 0})
                        category["bytes"] += size
                        category["files"] += 1
                except OSError:
                    continue
    for relative in sorted(
        (item for item in directory_bytes if item),
        key=lambda item: item.count("/"),
        reverse=True,
    ):
        parent = relative.rpartition("/")[0]
        directory_bytes[parent] += directory_bytes[relative]
        directory_files[parent] += directory_files[relative]
    selected_terminal_roots: list[tuple[str, str]] = []
    for relative, status in sorted(
        terminal_roots.items(),
        key=lambda item: (item[0].count("/"), item[0]),
    ):
        if any(
            relative == parent or relative.startswith(f"{parent}/")
            for parent, _ in selected_terminal_roots
        ):
            continue
        selected_terminal_roots.append((relative, status))
    cleanup_candidates = [
        {
            "path": relative,
            "status": status,
            "bytes": directory_bytes[relative],
            "reason": "terminal managed run; review before cleanup",
        }
        for relative, status in selected_terminal_roots
    ]
    return {
        "root": str(selected),
        "bytes": total_bytes,
        "files": total_files,
        "categories": dict(sorted(categories.items())),
        "linked_paths_not_followed": sorted(linked_paths),
        "cleanup_candidates": cleanup_candidates,
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
        raise RuntimeError(
            f"Project cache root identity does not match its project path: {root}"
        )
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


def project_cache_inventory(root: str | Path) -> dict[str, Any]:
    selected = Path(root).expanduser().resolve()
    aggregate = directory_inventory(selected)
    projects: list[dict[str, Any]] = []
    project_cleanup_candidates: list[dict[str, Any]] = []
    if selected.is_dir():
        for candidate in sorted(selected.iterdir(), key=lambda item: item.name.casefold()):
            if not candidate.is_dir() or candidate.is_symlink():
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
                "bytes": inventory["bytes"],
                "files": inventory["files"],
            }
            projects.append(record)
            if project_path is not None and project_exists is False:
                project_cleanup_candidates.append(
                    {
                        "path": candidate.name,
                        "project_path": project_path,
                        "bytes": inventory["bytes"],
                        "reason": "owned derived cache whose source project no longer exists",
                    }
                )
    aggregate["projects"] = projects
    aggregate["cleanup_candidates"] = [
        *aggregate["cleanup_candidates"],
        *sorted(project_cleanup_candidates, key=lambda item: item["bytes"], reverse=True),
    ]
    return aggregate


def _existing_volume_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    if not candidate.exists():
        raise RuntimeError(f"No existing volume ancestor for storage root: {path}")
    return candidate


def require_storage_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    maximum_managed_bytes: int,
    minimum_free_bytes: int,
    label: str,
) -> dict[str, Any]:
    if expected_new_bytes is None:
        raise RuntimeError(f"{label} storage preflight blocked: peak estimate is unknown")
    if (
        type(expected_new_bytes) is not int
        or expected_new_bytes < 0
        or maximum_managed_bytes <= 0
        or minimum_free_bytes <= 0
    ):
        raise ValueError("Storage budgets require non-negative estimates and positive limits")
    selected = Path(root).expanduser().resolve()
    inventory = directory_inventory(selected)
    usage = shutil.disk_usage(_existing_volume_path(selected))
    return _require_storage_budget_from_inventory(
        selected,
        inventory=inventory,
        usage=usage,
        expected_new_bytes=expected_new_bytes,
        maximum_managed_bytes=maximum_managed_bytes,
        minimum_free_bytes=minimum_free_bytes,
        label=label,
    )


def _require_storage_budget_from_inventory(
    selected: Path,
    *,
    inventory: dict[str, Any],
    usage: Any,
    expected_new_bytes: int,
    maximum_managed_bytes: int,
    minimum_free_bytes: int,
    label: str,
) -> dict[str, Any]:
    projected_managed = int(inventory["bytes"]) + expected_new_bytes
    projected_free = usage.free - expected_new_bytes
    report = {
        "schema": "mediaflow-storage-preflight/v1",
        "label": label,
        "root": str(selected),
        "policy": {
            "maximum_managed_bytes": maximum_managed_bytes,
            "minimum_free_bytes": minimum_free_bytes,
            "over_budget": "block-before-large-write",
            "cleanup": "report-only-until-authorized",
        },
        "current_managed_bytes": inventory["bytes"],
        "expected_new_bytes": expected_new_bytes,
        "projected_managed_bytes": projected_managed,
        "free_bytes": usage.free,
        "projected_free_bytes": projected_free,
        "cleanup_candidates": inventory["cleanup_candidates"],
    }
    failures = []
    if projected_managed > maximum_managed_bytes:
        failures.append("projected managed bytes exceed the configured limit")
    if projected_free < minimum_free_bytes:
        failures.append("projected free bytes fall below the configured safety line")
    if failures:
        raise RuntimeError(
            f"{label} storage preflight blocked: {'; '.join(failures)}\n"
            + json.dumps(report, ensure_ascii=False, sort_keys=True)
        )
    return report


def require_operation_storage_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    maximum_operation_bytes: int,
    minimum_free_bytes: int,
    label: str,
) -> dict[str, Any]:
    if expected_new_bytes is None:
        raise RuntimeError(f"{label} storage preflight blocked: peak estimate is unknown")
    if (
        type(expected_new_bytes) is not int
        or expected_new_bytes < 0
        or maximum_operation_bytes <= 0
        or minimum_free_bytes <= 0
    ):
        raise ValueError("Storage budgets require non-negative estimates and positive limits")
    selected = Path(root).expanduser().resolve()
    usage = shutil.disk_usage(_existing_volume_path(selected))
    projected_free = usage.free - expected_new_bytes
    report = {
        "schema": "mediaflow-storage-preflight/v1",
        "label": label,
        "root": str(selected),
        "policy": {
            "maximum_operation_bytes": maximum_operation_bytes,
            "minimum_free_bytes": minimum_free_bytes,
            "over_budget": "block-before-large-write",
            "cleanup": "report-only-until-authorized",
        },
        "expected_new_bytes": expected_new_bytes,
        "free_bytes": usage.free,
        "projected_free_bytes": projected_free,
        "cleanup_candidates": [],
    }
    failures = []
    if expected_new_bytes > maximum_operation_bytes:
        failures.append("operation peak exceeds the configured limit")
    if projected_free < minimum_free_bytes:
        failures.append("projected free bytes fall below the configured safety line")
    if failures:
        raise RuntimeError(
            f"{label} storage preflight blocked: {'; '.join(failures)}\n"
            + json.dumps(report, ensure_ascii=False, sort_keys=True)
        )
    return report


def require_runtime_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    return require_storage_budget(
        root,
        expected_new_bytes=expected_new_bytes,
        maximum_managed_bytes=policy.runtime_max_bytes,
        minimum_free_bytes=policy.minimum_free_bytes,
        label=label,
    )


def require_project_artifact_budget(
    project_dir: str | Path,
    owned_root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    project = Path(project_dir).expanduser().resolve()
    selected = Path(owned_root).expanduser().resolve()
    selected.relative_to(project)
    return {
        "artifact_root": require_storage_budget(
            selected,
            expected_new_bytes=expected_new_bytes,
            maximum_managed_bytes=policy.project_artifact_max_bytes,
            minimum_free_bytes=policy.minimum_free_bytes,
            label=label,
        ),
        "project": require_storage_budget(
            project,
            expected_new_bytes=expected_new_bytes,
            maximum_managed_bytes=policy.project_artifacts_max_bytes,
            minimum_free_bytes=policy.minimum_free_bytes,
            label="MediaFlow project artifacts",
        ),
    }


def require_delivery_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    return require_operation_storage_budget(
        root,
        expected_new_bytes=expected_new_bytes,
        maximum_operation_bytes=policy.delivery_operation_max_bytes,
        minimum_free_bytes=policy.minimum_free_bytes,
        label=label,
    )


def require_download_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    return require_operation_storage_budget(
        root,
        expected_new_bytes=expected_new_bytes,
        maximum_operation_bytes=policy.download_operation_max_bytes,
        minimum_free_bytes=policy.minimum_free_bytes,
        label=label,
    )


def _receipt_path(runtime_dir: str | Path, producer: str, operation_id: str) -> Path:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", producer) is None:
        raise ValueError(f"Invalid storage receipt producer: {producer}")
    identity = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:32]
    return (
        Path(runtime_dir).expanduser().resolve()
        / STORAGE_RECEIPT_DIRECTORY
        / producer
        / f"{identity}.json"
    )


def start_storage_receipt(
    runtime_dir: str | Path,
    *,
    producer: str,
    operation_id: str,
    owned_root: str | Path,
    preflight: dict[str, Any],
) -> Path:
    receipt = _receipt_path(runtime_dir, producer, operation_id)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": STORAGE_RECEIPT_SCHEMA,
        "producer": producer,
        "operation_id": operation_id,
        "status": "running",
        "owned_root": str(Path(owned_root).expanduser().resolve()),
        "preflight": preflight,
        "owner": {
            "pid": os.getpid(),
            "process_started": psutil.Process(os.getpid()).create_time(),
        },
        "outputs": [],
        "started_ns": time.time_ns(),
        "finished_ns": None,
        "error": "",
        "cleanup": "report-only-until-authorized",
    }
    atomic_write_text(receipt, f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n")
    return receipt


def finalize_storage_receipt(
    receipt: str | Path,
    *,
    status: str,
    outputs: tuple[str | Path, ...] = (),
    error: str = "",
) -> dict[str, Any]:
    if status not in {"passed", "failed", "interrupted"}:
        raise ValueError(f"Invalid terminal storage receipt status: {status}")
    selected = Path(receipt).expanduser().resolve(strict=True)
    payload = json.loads(selected.read_text(encoding="utf-8"))
    if payload.get("schema") != STORAGE_RECEIPT_SCHEMA:
        raise ValueError(f"Invalid storage receipt: {selected}")
    inventory = []
    for output in outputs:
        path = Path(output).expanduser().resolve()
        exists = path.exists()
        size = (
            path.stat().st_size
            if path.is_file()
            else int(directory_inventory(path)["bytes"])
            if path.is_dir()
            else 0
        )
        inventory.append({"path": str(path), "exists": exists, "bytes": size})
    payload.update(
        {
            "status": status,
            "outputs": inventory,
            "finished_ns": time.time_ns(),
            "error": error,
        }
    )
    atomic_write_text(selected, f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n")
    return payload


def storage_receipt_inventory(runtime_dir: str | Path) -> dict[str, Any]:
    root = Path(runtime_dir).expanduser().resolve() / STORAGE_RECEIPT_DIRECTORY
    records: list[dict[str, Any]] = []
    invalid: list[str] = []
    if root.is_dir():
        for receipt in sorted(root.rglob("*.json")):
            try:
                payload = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                invalid.append(receipt.relative_to(root).as_posix())
                continue
            if payload.get("schema") != STORAGE_RECEIPT_SCHEMA:
                invalid.append(receipt.relative_to(root).as_posix())
                continue
            status = str(payload.get("status") or "")
            owner_active = status == "running" and _storage_receipt_owner_active(
                payload.get("owner")
            )
            observed_status = (
                "interrupted" if status == "running" and not owner_active else status
            )
            retained_inventory: dict[str, Any] | None = None
            if observed_status == "interrupted":
                owned_root = Path(str(payload.get("owned_root") or "")).expanduser()
                if owned_root.exists():
                    retained_inventory = (
                        {"files": 1, "directories": 0, "bytes": owned_root.stat().st_size}
                        if owned_root.is_file()
                        else directory_inventory(owned_root)
                    )
            records.append(
                {
                    "producer": payload.get("producer"),
                    "operation_id": payload.get("operation_id"),
                    "status": status,
                    "observed_status": observed_status,
                    "owner": payload.get("owner"),
                    "owner_active": owner_active,
                    "owned_root": payload.get("owned_root"),
                    "retained_inventory": retained_inventory,
                    "outputs": payload.get("outputs", []),
                    "finished_ns": payload.get("finished_ns"),
                }
            )
    return {
        "root": str(root),
        "records": records,
        "invalid_receipts": invalid,
        "cleanup": "report-only-until-authorized",
    }


def _storage_receipt_owner_active(owner: object) -> bool:
    if not isinstance(owner, dict):
        return False
    pid = owner.get("pid")
    process_started = owner.get("process_started")
    if type(pid) is not int or not isinstance(process_started, int | float):
        return False
    try:
        process = psutil.Process(pid)
        return process.is_running() and abs(process.create_time() - float(process_started)) < 0.001
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False


def require_project_cache_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    if expected_new_bytes is None:
        raise RuntimeError(f"{label} storage preflight blocked: peak estimate is unknown")
    if type(expected_new_bytes) is not int or expected_new_bytes < 0:
        raise ValueError("Storage budgets require non-negative estimates and positive limits")
    policy = load_storage_policy()
    selected = Path(root).expanduser().resolve()
    projects_root = selected.parent
    ledger_path = projects_root.parent / PROJECT_CACHE_RESERVATION_LEDGER_FILENAME
    ledger_lock = ProcessFileLock(ledger_path.with_suffix(f"{ledger_path.suffix}.lock"))
    if not ledger_lock.acquire_until(timeout_seconds=PROJECT_CACHE_OWNER_LOCK_TIMEOUT_SECONDS):
        raise RuntimeError(f"Timed out reserving project cache capacity: {ledger_path}")
    try:
        observed_bytes, pending_bytes, ledger_is_valid = (
            _project_cache_reservation_state(
                ledger_path,
                projects_root,
            )
        )
        deferred_limit = min(
            PROJECT_CACHE_DEFERRED_RESERVATION_MAX_BYTES,
            max(
                1,
                policy.project_caches_max_bytes
                // PROJECT_CACHE_DEFERRED_RESERVATION_LIMIT_DIVISOR,
            ),
        )
        can_defer_global_inventory = (
            ledger_is_valid
            and pending_bytes + expected_new_bytes <= deferred_limit
        )
        usage = shutil.disk_usage(_existing_volume_path(projects_root))
        if can_defer_global_inventory:
            project_inventory = directory_inventory(selected)
            project_report = _require_storage_budget_from_inventory(
                selected,
                inventory=project_inventory,
                usage=usage,
                # Pending writes may belong to this project. Charging all of
                # them here is deliberately conservative and keeps the
                # per-project cap safe without a per-project reservation map.
                expected_new_bytes=pending_bytes + expected_new_bytes,
                maximum_managed_bytes=policy.project_cache_max_bytes,
                minimum_free_bytes=policy.minimum_free_bytes,
                label=label,
            )
            next_pending_bytes = pending_bytes + expected_new_bytes
            all_projects_report = _require_storage_budget_from_inventory(
                projects_root,
                inventory={
                    "bytes": observed_bytes,
                    "cleanup_candidates": [],
                },
                usage=usage,
                expected_new_bytes=next_pending_bytes,
                maximum_managed_bytes=policy.project_caches_max_bytes,
                minimum_free_bytes=policy.minimum_free_bytes,
                label="MediaFlow all project-derived caches",
            )
            _write_project_cache_pending_reservations(
                ledger_path,
                projects_root,
                observed_bytes=observed_bytes,
                pending_bytes=next_pending_bytes,
            )
            return {
                "project": project_report,
                "all_projects": _deferred_project_caches_report(
                    report=all_projects_report,
                    expected_new_bytes=expected_new_bytes,
                    pending_bytes=next_pending_bytes,
                    deferred_limit=deferred_limit,
                ),
            }

        all_projects_inventory = directory_inventory(projects_root)
        current_observed_bytes = int(all_projects_inventory["bytes"])
        observed_growth = (
            max(0, current_observed_bytes - observed_bytes)
            if ledger_is_valid
            else 0
        )
        remaining_pending_bytes = (
            max(0, pending_bytes - observed_growth) if ledger_is_valid else 0
        )
        reserved_new_bytes = remaining_pending_bytes + expected_new_bytes
        project_inventory = _project_inventory_from_aggregate(
            selected,
            all_projects_inventory,
        )
        result = {
            "project": _require_storage_budget_from_inventory(
                selected,
                inventory=project_inventory,
                usage=usage,
                expected_new_bytes=reserved_new_bytes,
                maximum_managed_bytes=policy.project_cache_max_bytes,
                minimum_free_bytes=policy.minimum_free_bytes,
                label=label,
            ),
            "all_projects": _require_storage_budget_from_inventory(
                projects_root,
                inventory=all_projects_inventory,
                usage=usage,
                expected_new_bytes=reserved_new_bytes,
                maximum_managed_bytes=policy.project_caches_max_bytes,
                minimum_free_bytes=policy.minimum_free_bytes,
                label="MediaFlow all project-derived caches",
            ),
        }
        _write_project_cache_pending_reservations(
            ledger_path,
            projects_root,
            observed_bytes=current_observed_bytes,
            pending_bytes=reserved_new_bytes,
        )
        return result
    finally:
        ledger_lock.release()


def _project_cache_reservation_state(
    ledger_path: Path,
    projects_root: Path,
) -> tuple[int, int, bool]:
    if not ledger_path.is_file():
        return 0, 0, False
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0, 0, False
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != PROJECT_CACHE_RESERVATION_LEDGER_SCHEMA
        or payload.get("projects_root") != str(projects_root)
        or type(payload.get("observed_bytes")) is not int
        or int(payload["observed_bytes"]) < 0
        or type(payload.get("pending_bytes")) is not int
        or int(payload["pending_bytes"]) < 0
    ):
        return 0, 0, False
    return int(payload["observed_bytes"]), int(payload["pending_bytes"]), True


def _write_project_cache_pending_reservations(
    ledger_path: Path,
    projects_root: Path,
    *,
    observed_bytes: int,
    pending_bytes: int,
) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        ledger_path,
        json.dumps(
            {
                "schema": PROJECT_CACHE_RESERVATION_LEDGER_SCHEMA,
                "projects_root": str(projects_root),
                "observed_bytes": observed_bytes,
                "pending_bytes": pending_bytes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        durable=True,
    )


def _project_inventory_from_aggregate(
    selected: Path,
    aggregate: dict[str, Any],
) -> dict[str, Any]:
    project_category = aggregate["categories"].get(
        selected.name,
        {"bytes": 0, "files": 0},
    )
    cleanup_prefix = f"{selected.name}/"
    return {
        "bytes": project_category["bytes"],
        "files": project_category["files"],
        "cleanup_candidates": [
            {
                **candidate,
                "path": str(candidate["path"])[len(cleanup_prefix) :],
            }
            for candidate in aggregate["cleanup_candidates"]
            if str(candidate["path"]).startswith(cleanup_prefix)
        ],
    }


def _deferred_project_caches_report(
    *,
    report: dict[str, Any],
    expected_new_bytes: int,
    pending_bytes: int,
    deferred_limit: int,
) -> dict[str, Any]:
    return {
        **report,
        "inventory_mode": "bounded-deferred-global-inventory",
        "requested_new_bytes": expected_new_bytes,
        "pending_reservation_bytes": pending_bytes,
        "deferred_reservation_limit_bytes": deferred_limit,
    }


def reserve_project_cache(
    root: str | Path,
    project_dir: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
    case_sensitive_paths: bool,
) -> None:
    require_project_cache_budget(
        root,
        expected_new_bytes=expected_new_bytes,
        label=label,
    )
    register_project_cache_owner(
        root,
        project_dir,
        case_sensitive_paths=case_sensitive_paths,
    )


def require_test_artifact_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    policy = load_storage_policy()
    return require_storage_budget(
        root,
        expected_new_bytes=expected_new_bytes,
        maximum_managed_bytes=policy.test_artifact_max_bytes,
        minimum_free_bytes=policy.minimum_free_bytes,
        label=label,
    )


def storage_policy_report() -> dict[str, Any]:
    return {
        "schema": "mediaflow-storage-policy/v1",
        **asdict(load_storage_policy()),
        "over_budget": "block-before-large-write",
        "cleanup": "report-only-until-authorized",
    }
