from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts import archive_windows_portable, build_windows_portable
from scripts.verify_portable_distribution import REQUIRED_DISTRIBUTIONS, verify

ROOT = Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_fixture(root: Path) -> Path:
    portable = root / "portable"
    internal = portable / "_internal"
    internal.mkdir(parents=True)
    (portable / "MediaFlow Pro.exe").write_bytes(b"portable-executable")
    (internal / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE Version 3")
    (internal / "THIRD_PARTY_NOTICES.md").write_text("third-party notices")
    (internal / "runtime.lock.json").write_bytes((ROOT / "runtime.lock.json").read_bytes())
    for name in sorted(REQUIRED_DISTRIBUTIONS):
        version = "2.0.0" if name == "mediaflow-pro" else "1.0.0"
        directory = internal / f"{name.replace('-', '_')}-{version}.dist-info"
        license_root = directory / "licenses"
        license_root.mkdir(parents=True)
        (directory / "METADATA").write_text(
            f"Name: {name}\nVersion: {version}\nLicense-Expression: MIT\n",
            encoding="utf-8",
        )
        (license_root / "LICENSE.txt").write_text("MIT", encoding="utf-8")

    notice_root = portable / "THIRD_PARTY_LICENSES" / "runtime"
    notice_root.mkdir(parents=True)
    rows: dict[str, dict[str, object]] = {}
    for component in (
        "chromium_project_license",
        "chromium_third_party_credits",
        "shotcut_copying",
        "shotcut_license",
    ):
        path = notice_root / f"{component}.txt"
        path.write_text(component, encoding="utf-8")
        rows[component] = {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    (notice_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "mediaflow-runtime-license-bundle/v1",
                "files": rows,
            }
        ),
        encoding="utf-8",
    )
    return portable


def test_final_portable_inventory_covers_every_file_and_required_license(
    tmp_path: Path,
) -> None:
    portable = _portable_fixture(tmp_path)

    report = verify(portable, expected_version="2.0.0")

    assert report["passed"] is True
    assert report["missing_required_distributions"] == []
    assert report["file_count"] == len(
        [path for path in portable.rglob("*") if path.is_file()]
    )
    assert all(row["sha256"] for row in report["files"])


def test_final_portable_inventory_rejects_build_or_user_state(
    tmp_path: Path,
) -> None:
    portable = _portable_fixture(tmp_path)
    cache_file = portable / "runtime" / "cache" / "projects" / "stale.mkv"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"stale")

    report = verify(portable, expected_version="2.0.0")

    assert report["passed"] is False
    assert report["mutable_files"] == ["runtime/cache/projects/stale.mkv"]


def test_final_portable_inventory_rejects_missing_direct_distribution_metadata(
    tmp_path: Path,
) -> None:
    portable = _portable_fixture(tmp_path)
    missing = portable / "_internal" / "mcp_types-1.0.0.dist-info"
    archived = portable / "_internal" / "mcp_types-1.0.0.archived"
    missing.replace(archived)

    report = verify(portable, expected_version="2.0.0")

    assert report["passed"] is False
    assert report["missing_required_distributions"] == ["mcp-types"]


@pytest.mark.parametrize("operation", ["build", "archive"])
def test_portable_preflight_reads_current_storage_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    if operation == "build" and os.name != "nt":
        pytest.skip("Windows portable build preflight")
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.bin").write_bytes(b"runtime data")
    (source / "runtime-copy.bin").hardlink_to(source / "runtime.bin")
    logical_bytes = 2 * len(b"runtime data")

    class PreflightReached(Exception):
        pass

    def check_budget(root: Path, *, expected_new_bytes: int, **_kwargs: object) -> dict:
        assert root == tmp_path
        assert expected_new_bytes == (
            logical_bytes * 2 + 4 * 1024**3 if operation == "build" else logical_bytes
        )
        raise PreflightReached

    if operation == "build":
        monkeypatch.setattr(
            build_windows_portable, "_require_release_source", lambda _tag: ("commit", "remote")
        )
        monkeypatch.setattr(build_windows_portable, "require_storage_budget", check_budget)
        with pytest.raises(PreflightReached):
            build_windows_portable.build(
                release_tag="v2.0.0", runtime_root=source,
                work_root=tmp_path / "work", output_root=tmp_path / "output",
            )
    else:
        monkeypatch.setattr(archive_windows_portable, "verify", lambda *_args, **_kwargs: {"passed": True})
        monkeypatch.setattr(archive_windows_portable, "require_storage_budget", check_budget)
        with pytest.raises(PreflightReached):
            archive_windows_portable.archive(
                source, tmp_path / "portable.7z", expected_version="2.0.0", receipt=tmp_path / "receipt.json",
            )
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / "portable.7z").exists()
