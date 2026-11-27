from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mediaflow.infrastructure import storage_inventory
from mediaflow.infrastructure.project_cache_reservations import (
    ProjectCacheLedger,
    ProjectCacheReservation,
    _acquire_project_cache_reservation_lock,
    _active_project_cache_reservations,
    _append_project_cache_reservation,
    _project_cache_pending_bytes,
    _project_cache_reservation_state,
    _record_project_observation,
    _write_project_cache_pending_reservations,
    cache_reservation_ledger_path,
    project_cache_inventory_refresh,
)
from mediaflow.infrastructure.storage_allocation import allocated_growth_bound
from mediaflow.infrastructure.storage_policy import (
    PROJECT_CACHE_DEFERRED_RESERVATION_LIMIT_DIVISOR,
    PROJECT_CACHE_DEFERRED_RESERVATION_MAX_BYTES,
    PROJECT_CACHE_RESERVATION_LEDGER_FILENAME,
    load_storage_policy,
)
from mediaflow.infrastructure.storage_preflight import (
    StorageBudgetExceeded,
    _existing_volume_path,
    _require_storage_budget_from_inventory,
)
from mediaflow.infrastructure.storage_receipts import start_storage_receipt
from mediaflow.infrastructure.storage_roots import (
    managed_root_ownership,
    register_owned_storage_roots,
    registered_cache_groups,
)


def require_project_cache_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
) -> dict[str, Any]:
    report, reservation = _acquire_project_cache_budget(
        root,
        expected_new_bytes=expected_new_bytes,
        label=label,
    )
    reservation.release()
    return report


def _acquire_project_cache_budget(
    root: str | Path,
    *,
    expected_new_bytes: int | None,
    label: str,
    expected_files: int = 1,
    check_cancelled: Callable[[], None] | None = None,
) -> tuple[dict[str, Any], ProjectCacheReservation]:
    if expected_new_bytes is None:
        raise RuntimeError(f"{label} storage preflight blocked: peak estimate is unknown")
    if type(expected_new_bytes) is not int or expected_new_bytes < 0:
        raise ValueError("Storage budgets require non-negative estimates and positive limits")
    policy = load_storage_policy()
    selected = Path(root).expanduser().resolve()
    projects_root = selected.parent
    ownership = managed_root_ownership(projects_root)
    ledger_path = cache_reservation_ledger_path()
    project_key = str(selected)
    allocated_peak = allocated_growth_bound(selected, expected_new_bytes, files=expected_files)
    deferred_limit = min(
        PROJECT_CACHE_DEFERRED_RESERVATION_MAX_BYTES,
        max(1, policy.project_caches_max_bytes // PROJECT_CACHE_DEFERRED_RESERVATION_LIMIT_DIVISOR),
    )
    # Register a changed runtime location under the same small control lock used
    # for admission. Filesystem scans still run without this lock.
    lock = _acquire_project_cache_reservation_lock(ledger_path)
    try:
        initial_groups = registered_cache_groups()
        if projects_root not in initial_groups:
            legacy = projects_root.parent / PROJECT_CACHE_RESERVATION_LEDGER_FILENAME
            if legacy != ledger_path and legacy.exists():
                _project_cache_reservation_state(legacy, projects_root)
            register_owned_storage_roots((projects_root,), ownership=ownership, producer="cache-projects")
    finally:
        lock.release()

    def read_state() -> ProjectCacheLedger:
        lock = _acquire_project_cache_reservation_lock(ledger_path)
        try:
            return _project_cache_reservation_state(ledger_path, projects_root)
        finally:
            lock.release()

    def inactive_ids(state: ProjectCacheLedger) -> set[str]:
        return set(state.reservations) - set(_active_project_cache_reservations(state.reservations))

    def admit(
        state: ProjectCacheLedger,
        project_inventory: dict[str, Any],
        all_inventory: dict[str, Any],
        *,
        deferred: bool,
    ) -> tuple[dict[str, Any], ProjectCacheReservation]:
        if check_cancelled is not None:
            check_cancelled()
        reservation_id, state.reservations = _append_project_cache_reservation(
            state.reservations, project_key=project_key, expected_new_bytes=allocated_peak
        )
        pending = _project_cache_pending_bytes(state.reservations)
        # All pending bytes are conservatively charged against the destination
        # volume. Roots on other volumes still contribute to the global quota.
        usage = shutil.disk_usage(_existing_volume_path(selected))
        result = {
            "project": _require_storage_budget_from_inventory(
                selected,
                inventory={**project_inventory, "allocated_bytes": state.observed_by_project[project_key]},
                usage=usage,
                expected_new_bytes=_project_cache_pending_bytes(state.reservations, project_key=project_key),
                maximum_managed_bytes=policy.project_cache_max_bytes,
                minimum_free_bytes=policy.minimum_free_bytes,
                label=label,
            ),
            "all_projects": _require_storage_budget_from_inventory(
                projects_root,
                inventory=all_inventory,
                usage=usage,
                expected_new_bytes=pending,
                maximum_managed_bytes=policy.project_caches_max_bytes,
                minimum_free_bytes=policy.minimum_free_bytes,
                label="MediaFlow all project-derived caches",
            ),
        }
        result["all_projects"]["registered_roots"] = list(state.registered_groups)
        if deferred:
            result["all_projects"] = _deferred_project_caches_report(
                report=result["all_projects"],
                expected_new_bytes=expected_new_bytes,
                pending_bytes=pending,
                deferred_limit=deferred_limit,
            )
        _write_project_cache_pending_reservations(ledger_path, projects_root, state)
        return result, ProjectCacheReservation(ledger_path, projects_root, reservation_id)

    def try_deferred() -> tuple[dict[str, Any], ProjectCacheReservation] | None:
        before = read_state()
        groups = tuple(str(path) for path in registered_cache_groups())
        if (
            before.valid
            and before.registered_groups == groups
            and not inactive_ids(before)
            and not before.shared_projects
            and _project_cache_pending_bytes(before.reservations) + allocated_peak <= deferred_limit
        ):
            project_inventory = storage_inventory.directory_inventory(
                selected, check_cancelled=check_cancelled,
            )
            lock = _acquire_project_cache_reservation_lock(ledger_path)
            try:
                latest = _project_cache_reservation_state(ledger_path, projects_root)
                if (
                    latest.valid
                    and latest.registered_groups == groups
                    and tuple(str(path) for path in registered_cache_groups()) == groups
                    and not inactive_ids(latest)
                    and not latest.shared_projects
                    and not project_inventory["has_hard_links"]
                    and _project_cache_pending_bytes(latest.reservations) + allocated_peak <= deferred_limit
                ):
                    _record_project_observation(
                        latest, project_key, project_inventory, before.observation_versions
                    )
                    try:
                        return admit(
                            latest,
                            project_inventory,
                            {
                                "allocated_bytes": sum(latest.observed_by_project.values()),
                                "cleanup_candidates": [],
                            },
                            deferred=True,
                        )
                    except StorageBudgetExceeded:
                        # Another project's completed files may have been removed
                        # since its last observation. Reconcile before refusing;
                        # a cached high-water mark must not retain freed capacity.
                        pass
            finally:
                lock.release()

        return None

    admitted = try_deferred()
    if admitted is not None:
        return admitted
    with project_cache_inventory_refresh(ledger_path, check_cancelled=check_cancelled):
        admitted = try_deferred()
        if admitted is not None:
            return admitted
        for _attempt in range(3):
            before = read_state()
            groups = tuple(str(path) for path in registered_cache_groups())
            dead_before_scan = inactive_ids(before)
            snapshot = storage_inventory.registered_roots_inventory(
                {path: Path(path) for path in groups}, check_cancelled=check_cancelled,
            )
            aggregate = snapshot["roots"][str(projects_root)]
            project_inventory = _project_inventory_from_aggregate(selected, aggregate)
            measured = {
                str(Path(group) / name): metrics
                for group, inventory in snapshot["roots"].items()
                for name, metrics in inventory["categories"].items()
            }
            measured.setdefault(project_key, project_inventory)
            lock = _acquire_project_cache_reservation_lock(ledger_path)
            try:
                if tuple(str(path) for path in registered_cache_groups()) != groups:
                    continue
                latest = _project_cache_reservation_state(ledger_path, projects_root)
                extra_allocated = 0
                for key in set(measured) | set(latest.observed_by_project):
                    metrics = measured.get(key, {"allocated_bytes": 0, "has_hard_links": False})
                    _record_project_observation(latest, key, metrics, before.observation_versions)
                    extra_allocated += max(
                        0, latest.observed_by_project[key] - int(metrics["allocated_bytes"]),
                    )
                latest.registered_groups = groups
                latest.valid = True
                latest.reservations = {
                    identity: value
                    for identity, value in latest.reservations.items()
                    if identity not in dead_before_scan
                }
                return admit(
                    latest,
                    project_inventory,
                    {
                        **snapshot["total"],
                        "allocated_bytes": snapshot["total"]["allocated_bytes"] + extra_allocated,
                        "cleanup_candidates": [],
                    },
                    deferred=False,
                )
            finally:
                lock.release()
        raise RuntimeError("Cache roots changed repeatedly during inventory; retry after producers settle")


def _project_inventory_from_aggregate(
    selected: Path,
    aggregate: dict[str, Any],
) -> dict[str, Any]:
    category = aggregate["categories"].get(
        selected.name,
        {
            "allocated_bytes": 0,
            "logical_bytes": 0,
            "unique_object_bytes": 0,
            "files": 0,
            "unique_objects": 0,
            "has_hard_links": False,
        },
    )
    prefix = f"{selected.name}/"
    return {
        **category,
        "cleanup_candidates": [
            {**candidate, "path": str(candidate["path"])[len(prefix) :]}
            for candidate in aggregate["cleanup_candidates"]
            if str(candidate["path"]).startswith(prefix)
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
    expected_files: int = 1,
    check_cancelled: Callable[[], None] | None = None,
) -> ProjectCacheReservation:
    if check_cancelled is not None:
        check_cancelled()
    selected = Path(root).expanduser().resolve()
    if selected.parent.name != "projects" or selected.parent.parent.name != "cache":
        raise ValueError(f"Project cache must come from RuntimePaths.project_cache_dir: {selected}")
    managed_root_ownership(selected)
    storage_inventory.register_project_cache_owner(
        selected,
        project_dir,
        case_sensitive_paths=case_sensitive_paths,
    )
    report, reservation = _acquire_project_cache_budget(
        selected,
        expected_new_bytes=expected_new_bytes,
        label=label,
        expected_files=expected_files,
        check_cancelled=check_cancelled,
    )
    try:
        reservation.receipt = start_storage_receipt(
            selected.parent.parent.parent,
            producer="project-derived-cache",
            operation_id=reservation.reservation_id,
            owned_root=selected,
            preflight=report,
            check_cancelled=check_cancelled,
        )
    except BaseException as error:
        reservation.release(error=error)
        raise
    return reservation
