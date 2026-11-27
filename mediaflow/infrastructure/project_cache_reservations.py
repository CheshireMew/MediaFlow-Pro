from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psutil

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure import storage_inventory
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.storage_policy import (
    PROJECT_CACHE_OWNER_LOCK_TIMEOUT_SECONDS,
    PROJECT_CACHE_RESERVATION_LEDGER_SCHEMA,
)
from mediaflow.infrastructure.storage_processes import storage_owner_is_active
from mediaflow.infrastructure.storage_receipts import finalize_storage_receipt, storage_terminal_status
from mediaflow.infrastructure.storage_roots import (
    managed_root_ownership,
    registered_cache_groups,
    storage_registry_path,
)


@dataclass(slots=True)
class ProjectCacheLedger:
    observed_by_project: dict[str, int]
    observation_versions: dict[str, int]
    reservations: dict[str, dict[str, Any]]
    shared_projects: set[str]
    valid: bool = True
    registered_groups: tuple[str, ...] = ()


@dataclass(slots=True)
class ProjectCacheReservation:
    ledger_path: Path
    projects_root: Path
    reservation_id: str
    receipt: Path | None = None
    _released: bool = False
    _completion_error: BaseException | None = None

    def __enter__(self) -> ProjectCacheReservation:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        error = _exc_info[1] if len(_exc_info) > 1 and isinstance(_exc_info[1], BaseException) else None
        self.release(error=error)

    def release(self, *, error: BaseException | None = None) -> None:
        if self._released:
            return
        if error is not None:
            self._completion_error = error
        error = self._completion_error
        try:
            self._settle(error=error)
        except Exception as settlement_error:
            if error is None:
                raise
            error.add_note(f"Cache allocation settlement remains pending: {settlement_error}")

    def _settle(self, *, error: BaseException | None) -> None:
        if self._released:
            return
        ledger_lock = _acquire_project_cache_reservation_lock(self.ledger_path)
        try:
            before = _project_cache_reservation_state(self.ledger_path, self.projects_root)
            if not before.valid or self.reservation_id not in before.reservations:
                raise RuntimeError(f"Cache reservation accounting was lost: {self.ledger_path}")
            project_key = str(before.reservations[self.reservation_id]["project"])
        finally:
            ledger_lock.release()

        # Keep filesystem work outside the global lock. A revision comparison
        # protects observations settled by another producer during this scan.
        try:
            inventory = storage_inventory.directory_inventory(self.projects_root / project_key)
        except Exception as measurement_error:
            ledger_lock = _acquire_project_cache_reservation_lock(self.ledger_path)
            try:
                latest = _project_cache_reservation_state(self.ledger_path, self.projects_root)
                if self.reservation_id in latest.reservations:
                    latest.reservations[self.reservation_id]["state"] = "settlement-required"
                    _write_project_cache_pending_reservations(self.ledger_path, self.projects_root, latest)
            finally:
                ledger_lock.release()
            if self.receipt is not None:
                finalize_storage_receipt(
                    self.receipt,
                    status="failed",
                    error=str(measurement_error),
                    inventory_error=measurement_error,
                )
            if error is not None:
                error.add_note(f"Cache allocation settlement remains pending: {measurement_error}")
                return
            raise
        ledger_lock = _acquire_project_cache_reservation_lock(self.ledger_path)
        try:
            latest = _project_cache_reservation_state(self.ledger_path, self.projects_root)
            if self._released:
                return
            if not latest.valid or self.reservation_id not in latest.reservations:
                raise RuntimeError(f"Cache reservation accounting was lost: {self.ledger_path}")
            _record_project_observation(latest, project_key, inventory, before.observation_versions)
            latest.reservations.pop(self.reservation_id)
            _write_project_cache_pending_reservations(self.ledger_path, self.projects_root, latest)
            self._released = True
        finally:
            ledger_lock.release()
        if self.receipt is not None:
            finalize_storage_receipt(
                self.receipt,
                status=storage_terminal_status(error),
                outputs=(self.projects_root / project_key,),
                error="" if error is None else str(error),
            )


def _acquire_project_cache_reservation_lock(ledger_path: Path) -> ProcessFileLock:
    ledger_lock = ProcessFileLock(ledger_path.with_suffix(f"{ledger_path.suffix}.lock"))
    if not ledger_lock.acquire_until(timeout_seconds=PROJECT_CACHE_OWNER_LOCK_TIMEOUT_SECONDS):
        raise RuntimeError(f"Timed out reserving project cache capacity: {ledger_path}")
    return ledger_lock


def _project_cache_reservation_state(
    ledger_path: Path,
    projects_root: Path,
) -> ProjectCacheLedger:
    if not ledger_path.is_file():
        return ProjectCacheLedger({}, {}, {}, set(), valid=False)
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cache reservation ledger is unreadable: {ledger_path}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid cache reservation ledger: {ledger_path}")
    observed_by_project = payload.get("observed_by_project")
    reservations = payload.get("reservations")
    if payload.get("schema") in {
        "mediaflow-project-cache-reservations/v3",
        "mediaflow-project-cache-reservations/v4",
    }:
        if not isinstance(reservations, dict) or any(
            not _is_project_cache_reservation(value) for value in reservations.values()
        ):
            raise RuntimeError(f"Invalid historical cache reservation ledger: {ledger_path}")
        if _active_project_cache_reservations(reservations):
            raise RuntimeError(
                "Finish or restart existing cache producers before upgrading their reservation ledger"
            )
        # Historical logical byte observations are rebuilt from current physical
        # files, never relabelled as allocation measurements.
        return ProjectCacheLedger({}, {}, {}, set(), valid=False)
    versions = payload.get("observation_versions")
    shared = payload.get("shared_projects")
    groups = payload.get("registered_groups")
    if (
        payload.get("schema") != PROJECT_CACHE_RESERVATION_LEDGER_SCHEMA
        or not isinstance(groups, list)
        or any(not isinstance(path, str) or not Path(path).is_absolute() for path in groups)
        or not _is_non_negative_integer_map(observed_by_project)
        or not _is_non_negative_integer_map(versions)
        or not isinstance(shared, list)
        or any(not isinstance(key, str) for key in shared)
        or not isinstance(reservations, dict)
    ):
        raise RuntimeError(f"Invalid cache reservation ledger: {ledger_path}")
    observed_map = cast(dict[str, int], observed_by_project)
    reservation_map = cast(dict[str, dict[str, Any]], reservations)
    registered = tuple(cast(list[str], groups))
    owned_groups = set(registered_cache_groups())
    for group in registered:
        # Runtime selection can change after these roots were registered. Their
        # persisted ownership still governs the old cache and its quota.
        if Path(group) not in owned_groups:
            managed_root_ownership(group)

    def valid_project(key: str) -> bool:
        return any(Path(key).parent == Path(group) for group in registered)

    if (
        any(
            not isinstance(reservation_id, str)
            or not reservation_id
            or not _is_project_cache_reservation(reservation)
            for reservation_id, reservation in reservation_map.items()
        )
        or any(
            value.get("state") not in {"running", "settlement-required"} for value in reservation_map.values()
        )
        or payload.get("observed_bytes") != sum(observed_map.values())
        or payload.get("pending_bytes") != _project_cache_pending_bytes(reservation_map)
        or any(not valid_project(key) for key in observed_map)
        or any(not valid_project(value["project"]) for value in reservation_map.values())
    ):
        raise RuntimeError(f"Inconsistent cache reservation ledger: {ledger_path}")
    return ProjectCacheLedger(
        dict(observed_map),
        dict(cast(dict[str, int], versions)),
        dict(reservation_map),
        set(shared),
        registered_groups=registered,
    )


def _record_project_observation(
    ledger: ProjectCacheLedger,
    project_key: str,
    inventory: dict[str, Any],
    before_versions: dict[str, int],
) -> None:
    measured = int(inventory["allocated_bytes"])
    concurrent = ledger.observation_versions.get(project_key, 0) != before_versions.get(project_key, 0)
    if concurrent:
        measured = max(measured, ledger.observed_by_project.get(project_key, 0))
    ledger.observed_by_project[project_key] = measured
    ledger.observation_versions[project_key] = ledger.observation_versions.get(project_key, 0) + 1
    if inventory.get("has_hard_links"):
        ledger.shared_projects.add(project_key)
    elif not concurrent:
        ledger.shared_projects.discard(project_key)


def _is_non_negative_integer_map(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and bool(key) and type(item) is int and item >= 0 for key, item in value.items()
    )


def _is_project_cache_reservation(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("project"), str)
        and bool(value["project"])
        and type(value.get("bytes")) is int
        and value["bytes"] >= 0
        and isinstance(value.get("owner"), dict)
    )


def _active_project_cache_reservations(
    reservations: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        reservation_id: reservation
        for reservation_id, reservation in reservations.items()
        if reservation.get("state") in {None, "running"} and storage_owner_is_active(reservation.get("owner"))
    }


def _project_cache_pending_bytes(
    reservations: dict[str, dict[str, Any]],
    *,
    project_key: str | None = None,
) -> int:
    return sum(
        int(reservation["bytes"])
        for reservation in reservations.values()
        if project_key is None or reservation["project"] == project_key
    )


def _append_project_cache_reservation(
    reservations: dict[str, dict[str, Any]],
    *,
    project_key: str,
    expected_new_bytes: int,
) -> tuple[str, dict[str, dict[str, Any]]]:
    reservation_id = uuid.uuid4().hex
    appended = dict(reservations)
    appended[reservation_id] = {
        "project": project_key,
        "bytes": expected_new_bytes,
        "state": "running",
        "owner": {
            "pid": os.getpid(),
            "process_started": psutil.Process(os.getpid()).create_time(),
        },
    }
    return reservation_id, appended


def _write_project_cache_pending_reservations(
    ledger_path: Path,
    projects_root: Path,
    state: ProjectCacheLedger,
) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        ledger_path,
        json.dumps(
            {
                "schema": PROJECT_CACHE_RESERVATION_LEDGER_SCHEMA,
                "registered_groups": list(state.registered_groups),
                "capacity_measure": "filesystem-file-allocation-upper-bound",
                "observed_bytes": sum(state.observed_by_project.values()),
                "pending_bytes": _project_cache_pending_bytes(state.reservations),
                "observed_by_project": state.observed_by_project,
                "observation_versions": state.observation_versions,
                "shared_projects": sorted(state.shared_projects),
                "reservations": state.reservations,
            },
            ensure_ascii=False,
            indent=2,
        ),
        durable=True,
    )


def cache_reservation_ledger_path() -> Path:
    from mediaflow.infrastructure.storage_policy import PROJECT_CACHE_RESERVATION_LEDGER_FILENAME

    return storage_registry_path().with_name(PROJECT_CACHE_RESERVATION_LEDGER_FILENAME)


@contextmanager
def project_cache_inventory_refresh(
    ledger_path: Path, *, check_cancelled: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Share cold inventory preparation without occupying the ledger write lock."""
    lock = ProcessFileLock(ledger_path.with_suffix(".inventory.lock"))
    deadline = time.monotonic() + 60
    while not lock.acquire():
        if check_cancelled is not None:
            check_cancelled()
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for project cache inventory refresh")
        time.sleep(.02)
    try:
        if check_cancelled is not None:
            check_cancelled()
        yield
    finally:
        lock.release()


def active_cache_roots() -> set[Path]:
    """Unknown or unsettled producers are retained, never offered for cleanup."""
    ledger = cache_reservation_ledger_path()
    if not ledger.exists():
        return set()
    lock = _acquire_project_cache_reservation_lock(ledger)
    try:
        state = _project_cache_reservation_state(ledger, ledger.parent)
        return {Path(value["project"]).resolve() for value in state.reservations.values()}
    finally:
        lock.release()
