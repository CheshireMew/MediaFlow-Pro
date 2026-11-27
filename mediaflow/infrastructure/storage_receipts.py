from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from pathlib import Path
from typing import Any

import psutil

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import TaskStatus
from mediaflow.infrastructure import storage_inventory
from mediaflow.infrastructure.storage_policy import (
    STORAGE_RECEIPT_DIRECTORY,
    STORAGE_RECEIPT_SCHEMA,
)
from mediaflow.infrastructure.storage_processes import storage_owner_is_active
from mediaflow.infrastructure.storage_roots import (
    forget_missing_storage_roots,
    managed_root_ownership,
    receipt_owns_path,
    register_owned_storage_roots,
)


def storage_terminal_status(error: BaseException | None) -> str:
    if error is None:
        return "passed"
    task_status = getattr(error, "status", None)
    if (
        not isinstance(error, Exception)
        or isinstance(error, CancelledError)
        or isinstance(task_status, TaskStatus)
        and task_status in {TaskStatus.CANCELLED, TaskStatus.PAUSED}
    ):
        return "interrupted"
    return "failed"


def _paths_snapshot(
    paths: tuple[Path, ...], *, check_cancelled: Callable[[], None] | None = None,
) -> dict[str, Any]:
    return storage_inventory.registered_roots_inventory(
        {str(index): path for index, path in enumerate(paths)}, check_cancelled=check_cancelled,
    )


def _receipt_path(runtime_dir: str | Path, producer: str, operation_id: str) -> Path:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", producer) is None:
        raise ValueError(f"Invalid storage receipt producer: {producer}")
    identity = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:32]
    return (
        Path(runtime_dir).expanduser().resolve() / STORAGE_RECEIPT_DIRECTORY / producer / f"{identity}.json"
    )


def start_storage_receipt(
    runtime_dir: str | Path,
    *,
    producer: str,
    operation_id: str,
    owned_root: str | Path,
    preflight: dict[str, Any],
    owned_roots: tuple[str | Path, ...] | None = None,
    artifact_classes: tuple[str, ...] = ("cache", "temporary", "evidence"),
    ownership: dict[str, str] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> Path:
    receipt = _receipt_path(runtime_dir, producer, operation_id)
    roots = tuple(Path(path).expanduser().resolve() for path in (owned_roots or (owned_root,)))
    ownership = ownership or preflight.get("ownership") or managed_root_ownership(owned_root)
    register_owned_storage_roots(
        (Path(runtime_dir).expanduser().resolve(),),
        ownership=managed_root_ownership(runtime_dir),
        producer="runtime-root",
        runtime_dir=runtime_dir,
    )
    register_owned_storage_roots(
        roots,
        ownership=ownership,
        producer=producer,
        receipt=receipt,
        runtime_dir=runtime_dir,
    )
    register_owned_storage_roots(
        (receipt.parent.parent,),
        ownership=managed_root_ownership(runtime_dir),
        producer="manifest-store",
        runtime_dir=runtime_dir,
    )
    payload = {
        "schema": STORAGE_RECEIPT_SCHEMA,
        "producer": producer,
        "operation_id": operation_id,
        "status": "running",
        "owned_root": str(Path(owned_root).expanduser().resolve()),
        "registered_roots": [str(path) for path in roots],
        "ownership": ownership,
        "registry_runtime_dir": str(Path(runtime_dir).expanduser().resolve()),
        "artifact_classes": list(artifact_classes),
        "baseline_inventory": _paths_snapshot(roots, check_cancelled=check_cancelled),
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
    receipt.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(receipt, f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n")
    return receipt


def finalize_storage_receipt(
    receipt: str | Path,
    *,
    status: str,
    outputs: tuple[str | Path, ...] = (),
    error: str = "",
    inventory_error: BaseException | None = None,
) -> dict[str, Any]:
    if status not in {"passed", "failed", "interrupted"}:
        raise ValueError(f"Invalid terminal storage receipt status: {status}")
    selected = Path(receipt).expanduser().resolve(strict=True)
    payload = json.loads(selected.read_text(encoding="utf-8"))
    if payload.get("schema") != STORAGE_RECEIPT_SCHEMA:
        raise ValueError(f"Invalid storage receipt: {selected}")
    inventory: list[dict[str, Any]] = []
    output_paths = tuple(Path(output).expanduser().resolve() for output in outputs)
    for path in output_paths:
        if not receipt_owns_path(payload["ownership"], path):
            raise RuntimeError(f"Storage output escaped its declared ownership: {path}")
        if inventory_error is not None:
            inventory.append({"path": str(path), "measurement": "unknown"})
            continue
        exists = path.exists()
        measured = _paths_snapshot((path,))["total"]
        inventory.append({"path": str(path), "exists": exists, **measured})
    owned_roots = tuple(Path(value) for value in payload["registered_roots"])
    observed: dict[str, Any] = (
        _paths_snapshot((*owned_roots, *output_paths))
        if inventory_error is None
        else {"status": "unknown", "error": str(inventory_error)}
    )
    payload.update(
        {
            "status": status,
            "outputs": inventory,
            "final_inventory": observed,
            "allocation_growth_observed_bytes": (
                observed["total"]["allocated_bytes"]
                - payload["baseline_inventory"]["total"]["allocated_bytes"]
                if inventory_error is None
                else None
            ),
            "allocation_attribution": (
                "registered-root snapshot; concurrent writes are not attributed exclusively to this run"
            ),
            "finished_ns": time.time_ns(),
            "error": error,
        }
    )
    atomic_write_text(selected, f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n")
    if status != "passed" and output_paths:
        register_owned_storage_roots(
            output_paths,
            ownership=payload["ownership"],
            producer=payload["producer"],
            receipt=selected,
            runtime_dir=payload["registry_runtime_dir"],
        )
    forget_missing_storage_roots(owned_roots, receipt=selected, runtime_dir=payload["registry_runtime_dir"])
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
            if not isinstance(payload, dict):
                invalid.append(receipt.relative_to(root).as_posix())
                continue
            current_schema = payload.get("schema") == STORAGE_RECEIPT_SCHEMA
            if not current_schema and payload.get("schema") != "mediaflow-storage-receipt/v1":
                invalid.append(receipt.relative_to(root).as_posix())
                continue
            if current_schema:
                try:
                    if (
                        not isinstance(payload.get("ownership"), dict)
                        or not isinstance(payload.get("registered_roots"), list)
                        or not payload["registered_roots"]
                        or any(
                            not receipt_owns_path(payload["ownership"], value)
                            for value in payload["registered_roots"]
                        )
                        or payload.get("status") not in {"running", "passed", "failed", "interrupted"}
                    ):
                        raise ValueError("receipt ownership or lifecycle is invalid")
                except (ValueError, TypeError, KeyError):
                    invalid.append(receipt.relative_to(root).as_posix())
                    continue
            status = str(payload.get("status") or "")
            owner_active = status == "running" and storage_owner_is_active(payload.get("owner"))
            observed_status = "interrupted" if status == "running" and not owner_active else status
            retained_inventory: dict[str, Any] | None = None
            if observed_status == "interrupted" and current_schema:
                retained_inventory = _paths_snapshot(
                    tuple(Path(value) for value in payload["registered_roots"])
                )
            records.append(
                {
                    "producer": payload.get("producer"),
                    "operation_id": payload.get("operation_id"),
                    "status": status,
                    "observed_status": observed_status,
                    "terminal_state": "succeeded" if observed_status == "passed" else observed_status,
                    "schema": payload.get("schema"),
                    "ownership_status": "registered" if current_schema else "historical-unverified",
                    "ownership": payload.get("ownership"),
                    "registered_roots": payload.get("registered_roots", []),
                    "artifact_classes": payload.get("artifact_classes", []),
                    "owner": payload.get("owner"),
                    "owner_active": owner_active,
                    "owned_root": payload.get("owned_root"),
                    "retained_inventory": retained_inventory,
                    "outputs": payload.get("outputs", []),
                    "final_inventory": payload.get("final_inventory"),
                    "finished_ns": payload.get("finished_ns"),
                }
            )
    return {
        "root": str(root),
        "records": records,
        "invalid_receipts": invalid,
        "cleanup": "report-only-until-authorized",
    }
