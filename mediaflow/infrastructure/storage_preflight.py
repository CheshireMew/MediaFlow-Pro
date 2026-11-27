from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from mediaflow.infrastructure import storage_inventory
from mediaflow.infrastructure.storage_allocation import allocated_growth_bound


class StorageBudgetExceeded(RuntimeError):
    """A complete observation or reservation exceeds an explicit storage limit."""


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
    registered_roots: dict[str, Path] | None = None,
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
    if registered_roots is None:
        inventory = storage_inventory.directory_inventory(selected)
    else:
        snapshot = storage_inventory.registered_roots_inventory(registered_roots)
        inventory = {**snapshot["total"], "cleanup_candidates": []}
    usage = shutil.disk_usage(_existing_volume_path(selected))
    report = _require_storage_budget_from_inventory(
        selected,
        inventory=inventory,
        usage=usage,
        expected_new_bytes=allocated_growth_bound(selected, expected_new_bytes),
        maximum_managed_bytes=maximum_managed_bytes,
        minimum_free_bytes=minimum_free_bytes,
        label=label,
    )
    if registered_roots is not None:
        report["registered_roots"] = [str(path) for path in registered_roots.values()]
    return report


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
    projected_managed = int(inventory["allocated_bytes"]) + expected_new_bytes
    projected_free = usage.free - expected_new_bytes
    report = {
        "schema": "mediaflow-storage-preflight/v2",
        "capacity_measure": "filesystem-file-allocation-upper-bound",
        "label": label,
        "root": str(selected),
        "policy": {
            "maximum_managed_bytes": maximum_managed_bytes,
            "minimum_free_bytes": minimum_free_bytes,
            "over_budget": "block-before-large-write",
            "cleanup": "report-only-until-authorized",
        },
        "current_managed_bytes": inventory["allocated_bytes"],
        "current_logical_bytes": inventory.get("logical_bytes"),
        "current_unique_object_bytes": inventory.get("unique_object_bytes"),
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
        raise StorageBudgetExceeded(
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
    expected_new_bytes = allocated_growth_bound(selected, expected_new_bytes)
    projected_free = usage.free - expected_new_bytes
    report = {
        "schema": "mediaflow-storage-preflight/v2",
        "capacity_measure": "filesystem-file-allocation-upper-bound",
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
