# ruff: noqa: E402

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.runtime_capabilities import RUNTIME_CAPABILITY_IDS
from mediaflow.infrastructure.runtime_capabilities import (
    RuntimeCapabilityInspector,
)
from scripts.run_artifacts import verification_run


def verify(run_dir: Path) -> int:
    inspection = RuntimeCapabilityInspector().inspect()
    statuses = {item.id: item for item in inspection.capabilities}
    missing = RUNTIME_CAPABILITY_IDS - statuses.keys()
    failed = [
        item
        for item in inspection.capabilities
        if item.id in RUNTIME_CAPABILITY_IDS and item.status != "ready"
    ]
    passed = not missing and not failed
    report = {
        "schema_version": 2,
        **inspection.model_dump(mode="json"),
        "passed": passed,
    }
    report_path = run_dir / "development-runtime-report.json"
    atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
    print(report_path)
    if not passed:
        reasons = [
            *(f"{item.id}: {item.reason}" for item in failed),
            *(f"{item}: capability was not inspected" for item in sorted(missing)),
        ]
        raise RuntimeError(
            "Development runtime verification failed: " + "; ".join(reasons)
        )
    return 0


def main() -> int:
    with verification_run("development-runtime") as run_dir:
        return verify(run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
