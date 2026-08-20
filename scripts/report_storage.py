from __future__ import annotations

import argparse
import json
from pathlib import Path

from mediaflow.environment import load_project_environment, test_run_root
from mediaflow.infrastructure.runtime_paths import configured_runtime_directory
from mediaflow.infrastructure.storage_budget import (
    directory_inventory,
    project_cache_inventory,
    storage_policy_report,
    storage_receipt_inventory,
)


def build_report() -> dict[str, object]:
    load_project_environment()
    runtime = configured_runtime_directory()
    roots: dict[str, Path] = {"test_artifacts": test_run_root()}
    if runtime is not None:
        roots["project_derived_caches"] = runtime / "cache" / "projects"
        roots["runtime_components"] = runtime / "tools"
        roots["runtime_downloads"] = runtime / "downloads"
    inventories = {
        name: (
            project_cache_inventory(path)
            if name == "project_derived_caches"
            else directory_inventory(path)
        )
        for name, path in roots.items()
    }
    return {
        "schema": "mediaflow-storage-review/v1",
        "policy": storage_policy_report(),
        "roots": inventories,
        "operation_receipts": (
            storage_receipt_inventory(runtime)
            if runtime is not None
            else {"status": "runtime-directory-not-configured"}
        ),
        "cleanup": "report-only-until-authorized",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report MediaFlow-owned storage without changing or deleting files",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build_report(),
            ensure_ascii=False,
            indent=None if args.compact else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
