from __future__ import annotations

import argparse
import json
from pathlib import Path

from mediaflow.environment import load_project_environment, test_run_root
from mediaflow.infrastructure.project_cache_reservations import active_cache_roots
from mediaflow.infrastructure.runtime_paths import configured_runtime_directory
from mediaflow.infrastructure.storage_inventory import project_cache_inventory, registered_roots_inventory
from mediaflow.infrastructure.storage_policy import storage_policy_report
from mediaflow.infrastructure.storage_processes import storage_owner_is_active
from mediaflow.infrastructure.storage_receipts import storage_receipt_inventory
from mediaflow.infrastructure.storage_roots import (
    read_storage_root_registry,
    registered_cache_groups,
    registered_storage_roots,
)


def build_report() -> dict[str, object]:
    load_project_environment()
    runtime = configured_runtime_directory()
    registry = read_storage_root_registry(runtime)
    stores = {
        Path(record["path"]).parent
        for record in registry["roots"].values()
        if record["producer"] == "manifest-store"
    }
    if runtime is not None:
        stores.add(runtime)
    receipt_reports = [storage_receipt_inventory(path) for path in sorted(stores)]
    receipts = {
        "records": [record for report in receipt_reports for record in report["records"]],
        "invalid_receipts": [
            str(Path(report["root"]) / value)
            for report in receipt_reports
            for value in report["invalid_receipts"]
        ],
    }
    if receipts["invalid_receipts"]:
        raise RuntimeError("Storage manifests require review: " + ", ".join(receipts["invalid_receipts"]))
    roots = registered_storage_roots(test_root=test_run_root(), runtime_dir=runtime, receipts=receipts)
    snapshot = registered_roots_inventory(roots)
    retained = active_cache_roots()
    groups = set(registered_cache_groups())
    if runtime is not None:
        groups.add(runtime / "cache" / "projects")
    caches = {str(path): project_cache_inventory(path, retained_roots=retained) for path in sorted(groups)}
    run_manifests = []
    for record in registry["roots"].values():
        if record["producer"] not in {"pytest-and-verification-evidence", "local-quality-run"}:
            continue
        manifest = record.get("receipt")
        if not manifest or not Path(manifest).exists():
            continue
        payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
        status = payload.get("status")
        if status not in {"running", "passed", "failed", "interrupted"}:
            raise RuntimeError(f"Invalid run manifest lifecycle: {manifest}")
        owner_active = status == "running" and storage_owner_is_active(payload.get("owner"))
        observed = "interrupted" if status == "running" and not owner_active else status
        run_manifests.append(
            {
                "manifest": manifest,
                "producer": record["producer"],
                "status": status,
                "terminal_state": "succeeded" if observed == "passed" else observed,
                "owner_active": owner_active,
                "storage": payload.get("storage"),
            }
        )
    return {
        "schema": "mediaflow-storage-review/v2",
        "policy": storage_policy_report(),
        "roots": snapshot["roots"],
        "total": snapshot["total"],
        "measurement": "filesystem-file-allocation-upper-bound",
        "project_caches": caches,
        "operation_receipts": receipts,
        "run_manifests": run_manifests,
        "cleanup": "report-only-until-authorized",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report MediaFlow-owned storage without changing or deleting files",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    try:
        report = build_report()
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        print(
            json.dumps(
                {"schema": "mediaflow-storage-review/v2", "status": "blocked", "error": str(error)},
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
