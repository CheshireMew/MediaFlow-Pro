# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
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
from scripts.generate_runtime_license_bundle import generate as generate_runtime_licenses
from scripts.verify_portable_distribution import verify as verify_portable_distribution


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def _require_release_source(release_tag: str) -> tuple[str, str]:
    if _git("status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("Portable release builds require a clean source worktree")
    commit = _git("rev-parse", "HEAD")
    tag_commit = _git("rev-list", "-n", "1", release_tag)
    if commit != tag_commit:
        raise RuntimeError(f"Release tag {release_tag} does not point to checked-out HEAD")
    exact_tags = set(_git("tag", "--points-at", "HEAD").splitlines())
    if release_tag not in exact_tags:
        raise RuntimeError(f"Checked-out commit is not tagged as {release_tag}")
    return commit, _git("remote", "get-url", "origin")


def _render_icon(destination: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication([])
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer = QSvgRenderer(str(ROOT / "mediaflow/resources/branding/mediaflow-mark.svg"))
    if not renderer.isValid():
        raise RuntimeError("MediaFlow application icon SVG is invalid")
    renderer.render(painter, QRectF(0, 0, 256, 256))
    painter.end()
    if not image.save(str(destination), "ICO"):
        raise RuntimeError("Unable to render the Windows application icon")
    if QGuiApplication.instance() is app:
        app.quit()


def _copy_runtime(runtime_root: Path, portable_root: Path) -> None:
    target = portable_root / "runtime"
    target.mkdir()
    for name in ("deps", "native"):
        source = runtime_root / name
        if not source.is_dir():
            raise FileNotFoundError(f"Prepared runtime is missing {source}")
        shutil.copytree(source, target / name, copy_function=shutil.copy2)


def build(
    *,
    release_tag: str,
    runtime_root: Path,
    work_root: Path,
    output_root: Path,
) -> Path:
    if os.name != "nt":
        raise RuntimeError("Windows portable builds must run on Windows")
    runtime_root = runtime_root.expanduser().resolve()
    work_root = work_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    for selected, label in ((work_root, "work"), (output_root, "output")):
        if ROOT == selected or ROOT in selected.parents:
            raise RuntimeError(f"Portable {label} root must be outside the source repository")
        if selected.exists():
            raise FileExistsError(f"Portable {label} root already exists: {selected}")
    if work_root.drive.casefold() != output_root.drive.casefold():
        raise RuntimeError("Portable work and output roots must share a volume for publication")
    if not runtime_root.is_dir():
        raise FileNotFoundError(runtime_root)

    commit, remote = _require_release_source(release_tag)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(project["project"]["version"])
    runtime_bytes = int(directory_inventory(runtime_root)["bytes"])
    policy = load_storage_policy()
    storage_preflight = require_storage_budget(
        work_root.parent,
        expected_new_bytes=runtime_bytes * 2 + 4 * 1024**3,
        maximum_managed_bytes=policy.delivery_operation_max_bytes,
        minimum_free_bytes=policy.minimum_free_bytes,
        label="MediaFlow Windows portable build",
    )

    work_root.mkdir(parents=True)
    icon_path = work_root / "mediaflow.ico"
    _render_icon(icon_path)
    spec_path = ROOT / "packaging/windows/mediaflow_portable.spec"
    pyinstaller_dist = work_root / "pyinstaller-dist"
    pyinstaller_work = work_root / "pyinstaller-work"
    environment = dict(os.environ)
    environment["MEDIAFLOW_PORTABLE_ICON"] = str(icon_path)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(pyinstaller_dist),
            "--workpath",
            str(pyinstaller_work),
            str(spec_path),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    portable_root = pyinstaller_dist / "MediaFlow Pro"
    if not (portable_root / "MediaFlow Pro.exe").is_file():
        raise RuntimeError("PyInstaller did not produce the expected portable executable")
    _copy_runtime(runtime_root, portable_root)
    generate_runtime_licenses(
        portable_root / "runtime",
        portable_root / "THIRD_PARTY_LICENSES" / "runtime",
    )

    provenance = {
        "schema": "mediaflow-windows-portable-build/v1",
        "release_tag": release_tag,
        "version": version,
        "commit": commit,
        "source_remote": remote,
        "requirements_sha256": sha256(ROOT / "requirements.lock"),
        "runtime_lock_sha256": sha256(ROOT / "runtime.lock.json"),
        "spec_sha256": sha256(spec_path),
        "python": sys.version,
        "storage_preflight": storage_preflight,
    }
    atomic_write_text(
        portable_root / "BUILD-PROVENANCE.json",
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True),
    )
    report = verify_portable_distribution(portable_root, expected_version=version)
    atomic_write_text(
        work_root / "portable-distribution-report.json",
        json.dumps(report, ensure_ascii=False, indent=2),
    )
    if not report["passed"]:
        raise RuntimeError("Final portable directory failed its exact inventory gate")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    portable_root.replace(output_root)
    return output_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an auditable Windows portable directory from an exact release tag"
    )
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    output = build(
        release_tag=arguments.release_tag,
        runtime_root=arguments.runtime_root,
        work_root=arguments.work_root,
        output_root=arguments.output_root,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
