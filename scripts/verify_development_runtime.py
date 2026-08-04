# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.runtime_capabilities import RUNTIME_CAPABILITY_PROFILES
from mediaflow.infrastructure.runtime_capabilities import (
    RuntimeCapabilityInspector,
)
from mediaflow.infrastructure.runtime_context import RuntimeContext
from scripts.run_artifacts import verification_run


def verify(run_dir: Path, *, profile: str) -> int:
    required_ids = RUNTIME_CAPABILITY_PROFILES[profile]
    inspection = RuntimeCapabilityInspector(runtime=RuntimeContext.discover()).inspect()
    statuses = {item.id: item for item in inspection.capabilities}
    missing = required_ids - statuses.keys()
    failed = [
        item
        for item in inspection.capabilities
        if item.id in required_ids and item.status != "ready"
    ]
    passed = not missing and not failed
    report = {
        "schema_version": 2,
        "profile": profile,
        "required_capabilities": sorted(required_ids),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=tuple(RUNTIME_CAPABILITY_PROFILES),
        default="core",
    )
    args = parser.parse_args(argv)
    with verification_run("development-runtime") as run_dir:
        return verify(run_dir, profile=args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
