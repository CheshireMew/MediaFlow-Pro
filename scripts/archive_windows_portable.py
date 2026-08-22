# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure.storage_budget import (
    directory_inventory,
    load_storage_policy,
    require_storage_budget,
)
from scripts.verify_portable_distribution import verify


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive(
    portable_root: Path,
    output: Path,
    *,
    expected_version: str,
    receipt: Path,
) -> dict[str, object]:
    portable_root = portable_root.expanduser().resolve()
    output = output.expanduser().resolve()
    receipt = receipt.expanduser().resolve()
    if output.exists() or receipt.exists():
        raise FileExistsError("Portable archive output or receipt already exists")
    if portable_root == output.parent or portable_root in output.parents:
        raise RuntimeError("Portable archive must be written outside the portable directory")
    verification = verify(portable_root, expected_version=expected_version)
    if not verification["passed"]:
        raise RuntimeError("Portable directory failed verification before archive creation")
    source_bytes = int(directory_inventory(portable_root)["bytes"])
    policy = load_storage_policy()
    storage_preflight = require_storage_budget(
        output.parent,
        expected_new_bytes=source_bytes,
        maximum_managed_bytes=policy.delivery_operation_max_bytes,
        minimum_free_bytes=policy.minimum_free_bytes,
        label="MediaFlow Windows portable archive",
    )
    import py7zr

    output.parent.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(output, mode="x") as archive_file:
        archive_file.writeall(portable_root, arcname=portable_root.name)
    report = {
        "schema": "mediaflow-windows-portable-archive/v1",
        "portable_root": str(portable_root),
        "portable_file_count": verification["file_count"],
        "portable_total_bytes": verification["total_bytes"],
        "archive": str(output),
        "archive_bytes": output.stat().st_size,
        "archive_sha256": sha256(output),
        "storage_preflight": storage_preflight,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(receipt, json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archive a verified Windows portable directory exactly once"
    )
    parser.add_argument("--portable-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    arguments = parser.parse_args(argv)
    report = archive(
        arguments.portable_root,
        arguments.output,
        expected_version=arguments.expected_version,
        receipt=arguments.receipt,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
