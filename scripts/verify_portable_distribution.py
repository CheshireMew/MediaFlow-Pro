# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure.runtime_contract import load_runtime_contract
from scripts.run_artifacts import verification_run

SCHEMA = "mediaflow-portable-distribution-inventory/v1"
REQUIRED_DISTRIBUTIONS = {
    "aiohttp",
    "faster-whisper",
    "json-repair",
    "mcp",
    "mcp-types",
    "mediaflow-pro",
    "openai",
    "opencv-python-headless",
    "playwright",
    "psutil",
    "pydantic",
    "pyside6",
    "pywin32",
    "yt-dlp",
}
LICENSE_NAME = re.compile(r"(?i)(?:license|licence|copying|notice|copyright)")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _distribution_rows(internal: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for directory in sorted(internal.glob("*.dist-info"), key=lambda item: item.name.lower()):
        metadata_path = directory / "METADATA"
        if not metadata_path.is_file():
            raise RuntimeError(f"Portable distribution metadata is missing: {metadata_path}")
        metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8", errors="replace"))
        name = str(metadata.get("Name") or "").strip()
        version = str(metadata.get("Version") or "").strip()
        license_value = str(
            metadata.get("License-Expression") or metadata.get("License") or ""
        ).strip()
        license_files = sorted(
            _relative(path, directory)
            for path in directory.rglob("*")
            if path.is_file() and LICENSE_NAME.search(path.name)
        )
        passed = bool(name and version and license_value and license_files)
        rows.append(
            {
                "name": name,
                "canonical_name": canonical_name(name),
                "version": version,
                "license": license_value,
                "license_files": license_files,
                "passed": passed,
            }
        )
    return rows


def _runtime_notice_rows(portable_root: Path) -> list[dict[str, object]]:
    notice_root = portable_root / "THIRD_PARTY_LICENSES" / "runtime"
    manifest_path = notice_root / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Final portable directory has no runtime license manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "mediaflow-runtime-license-bundle/v1":
        raise RuntimeError("Unsupported runtime license manifest")
    records = manifest.get("files")
    if not isinstance(records, dict) or len(records) != 4:
        raise RuntimeError("Runtime license manifest is incomplete")
    rows: list[dict[str, object]] = []
    for component, raw in sorted(records.items()):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Runtime license entry is invalid: {component}")
        relative = str(raw.get("path") or "")
        path = notice_root / relative
        digest = sha256(path) if path.is_file() else ""
        row = {
            "component": component,
            "path": _relative(path, portable_root),
            "bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": digest,
            "passed": (
                path.is_file()
                and digest == raw.get("sha256")
                and path.stat().st_size == raw.get("bytes")
            ),
        }
        rows.append(row)
    return rows


def verify(portable_root: Path, *, expected_version: str | None) -> dict[str, object]:
    portable_root = portable_root.expanduser().resolve()
    if not portable_root.is_dir():
        raise FileNotFoundError(portable_root)
    internal = portable_root / "_internal"
    executable = portable_root / "MediaFlow Pro.exe"
    required_files = [
        executable,
        internal / "LICENSE",
        internal / "THIRD_PARTY_NOTICES.md",
        internal / "runtime.lock.json",
    ]
    missing = [_relative(path, portable_root) for path in required_files if not path.is_file()]
    if missing:
        raise RuntimeError(f"Portable distribution is missing required files: {missing}")

    distributions = _distribution_rows(internal)
    present = {str(row["canonical_name"]) for row in distributions}
    missing_distributions = sorted(REQUIRED_DISTRIBUTIONS - present)
    mediaflow_rows = [row for row in distributions if row["canonical_name"] == "mediaflow-pro"]
    version_passed = (
        expected_version is None
        or (
            len(mediaflow_rows) == 1
            and mediaflow_rows[0]["version"] == expected_version
        )
    )
    runtime_notices = _runtime_notice_rows(portable_root)
    mutable_files = sorted(
        _relative(path, portable_root)
        for root in (
            portable_root / "UserData",
            portable_root / "runtime" / "cache",
            portable_root / "runtime" / "logs",
        )
        if root.exists()
        for path in root.rglob("*")
        if path.is_file()
    )
    files = [
        {
            "path": _relative(path, portable_root),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(
            (path for path in portable_root.rglob("*") if path.is_file()),
            key=lambda item: _relative(item, portable_root).lower(),
        )
    ]
    contract = load_runtime_contract(internal / "runtime.lock.json")
    report = {
        "schema": SCHEMA,
        "portable_root": str(portable_root),
        "target": contract.target.key,
        "expected_version": expected_version,
        "version_passed": version_passed,
        "required_files": [_relative(path, portable_root) for path in required_files],
        "python_distributions": distributions,
        "missing_required_distributions": missing_distributions,
        "runtime_notices": runtime_notices,
        "mutable_files": mutable_files,
        "file_count": len(files),
        "total_bytes": sum(int(row["bytes"]) for row in files),
        "files": files,
    }
    report["passed"] = (
        version_passed
        and not missing_distributions
        and distributions
        and all(bool(row["passed"]) for row in distributions)
        and all(bool(row["passed"]) for row in runtime_notices)
        and not mutable_files
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inventory and verify the exact final Windows portable directory"
    )
    parser.add_argument("--portable-root", type=Path, required=True)
    parser.add_argument("--expected-version")
    parser.add_argument("--output-dir", type=Path)
    arguments = parser.parse_args(argv)
    with verification_run(
        "portable-distribution",
        explicit_parent=arguments.output_dir,
    ) as run_dir:
        report = verify(
            arguments.portable_root,
            expected_version=arguments.expected_version,
        )
        report_path = run_dir / "portable-distribution-report.json"
        atomic_write_text(
            report_path,
            json.dumps(report, ensure_ascii=False, indent=2),
        )
        print(report_path)
        if not report["passed"]:
            raise RuntimeError(
                "Portable distribution inventory failed: "
                f"missing={report['missing_required_distributions']}, "
                f"mutable_files={len(report['mutable_files'])}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
