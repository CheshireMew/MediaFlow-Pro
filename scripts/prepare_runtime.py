from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import stat
import subprocess
import tarfile
import urllib.request
import uuid
import zipfile
from pathlib import Path

from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.runtime_contract import (
    PlatformTarget,
    RuntimeContract,
    load_runtime_contract,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths, runtime_directory


def _archive_failure(runtime_root: Path, path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    archive = runtime_root / "archive" / "runtime-preparation"
    archive.mkdir(parents=True, exist_ok=True)
    destination = archive / f"{label}-{uuid.uuid4().hex}"
    path.replace(destination)
    return destination


def _download(
    runtime_root: Path,
    *,
    url: str,
    sha256: str,
) -> Path:
    download_root = runtime_root / "downloads"
    download_root.mkdir(parents=True, exist_ok=True)
    destination = download_root / Path(url).name
    if destination.is_file():
        actual = sha256_file(destination)
        if actual != sha256:
            raise RuntimeError(
                f"Pinned archive checksum mismatch: {destination} ({actual})"
            )
        return destination
    temporary = download_root / f"download-{uuid.uuid4().hex}.part"
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "MediaFlow-Pro-Runtime/2"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open(
            "wb"
        ) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = sha256_file(temporary)
        if actual != sha256:
            raise RuntimeError(
                f"Downloaded archive checksum mismatch: {temporary} ({actual})"
            )
        temporary.replace(destination)
        return destination
    except BaseException as error:
        archived = _archive_failure(runtime_root, temporary, "download-failed")
        if archived is not None:
            error.add_note(f"Failed download archived at {archived}")
        raise


def _extract_archive(
    archive: Path,
    destination: Path,
    archive_format: str,
) -> None:
    if archive_format == "zip":
        with zipfile.ZipFile(archive) as source:
            source.extractall(destination)
        _restore_zip_permissions(archive, destination)
        return
    if archive_format == "txz":
        with tarfile.open(archive, mode="r:xz") as source:
            source.extractall(destination, filter="data")
        return
    if archive_format != "dmg":
        raise ValueError(f"Unsupported runtime archive format: {archive_format}")
    if PlatformTarget.current().operating_system != "macos":
        raise RuntimeError("DMG runtime archives can only be prepared on macOS")
    completed = subprocess.run(
        [
            "hdiutil",
            "attach",
            "-plist",
            "-nobrowse",
            "-readonly",
            str(archive),
        ],
        check=True,
        capture_output=True,
    )
    document = plistlib.loads(completed.stdout)
    entities = document.get("system-entities", [])
    mount_point = next(
        (
            Path(item["mount-point"])
            for item in entities
            if isinstance(item, dict) and item.get("mount-point")
        ),
        None,
    )
    if mount_point is None:
        raise RuntimeError("Shotcut DMG did not publish a mount point")
    try:
        app = mount_point / "Shotcut.app"
        if not app.is_dir():
            raise FileNotFoundError(app)
        target = destination / "Shotcut" / "Shotcut.app"
        target.parent.mkdir(parents=True)
        shutil.copytree(app, target, symlinks=True)
    finally:
        subprocess.run(
            ["hdiutil", "detach", str(mount_point)],
            check=True,
        )


def _restore_zip_permissions(archive: Path, destination: Path) -> None:
    if os.name == "nt":
        return
    root = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            mode = (member.external_attr >> 16) & 0o777
            if mode == 0:
                continue
            extracted = (destination / member.filename).resolve()
            if not extracted.is_relative_to(root):
                raise RuntimeError(f"Archive member escapes the runtime root: {member.filename}")
            if extracted.exists():
                extracted.chmod(mode)


def _require_runtime_layout(paths: RuntimePaths) -> None:
    required_files = {
        "ffmpeg": paths.ffmpeg,
        "ffprobe": paths.ffprobe,
        "melt": paths.melt,
        "mlt_library": paths.mlt_library,
        "chromium": paths.chromium,
    }
    missing_files = [
        f"{name}: {path}"
        for name, path in required_files.items()
        if path is None or not path.is_file()
    ]
    required_directories = {
        "mlt_root": paths.mlt_root,
        "mlt_repository": paths.mlt_repository,
        "mlt_preview_repository": paths.mlt_preview_repository,
        "mlt_data": paths.mlt_data,
    }
    missing_directories = [
        f"{name}: {path}"
        for name, path in required_directories.items()
        if path is None or not path.is_dir()
    ]
    missing = [*missing_files, *missing_directories]
    if missing:
        raise RuntimeError("Pinned runtime layout is incomplete: " + "; ".join(missing))


def _make_executable(path: Path | None) -> None:
    if path is None or os.name == "nt":
        return
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _prepare_windows_preview_repository(bundle_root: Path) -> None:
    if os.name != "nt":
        return
    source = bundle_root / "lib" / "mlt"
    destination = bundle_root / "lib" / "mlt-preview"
    if not source.is_dir():
        raise FileNotFoundError(source)
    excluded = {"libmltqt6.dll", "libmltglaxnimate-qt6.dll"}
    destination.mkdir(parents=True, exist_ok=True)
    for plugin in source.iterdir():
        if not plugin.is_file() or plugin.name in excluded:
            continue
        target = destination / plugin.name
        if not target.exists():
            os.link(plugin, target)


def _prepare_bundle(runtime_root: Path, contract: RuntimeContract) -> None:
    bundle = contract.reviewed_bundle
    install_root = runtime_root / "deps" / f"{bundle.provider}-{bundle.version}"
    bundle_root = contract.reviewed_bundle_directory(runtime_root)
    if bundle_root.is_dir():
        if contract.target.operating_system == "windows":
            _prepare_windows_preview_repository(bundle_root)
        return
    if install_root.exists():
        raise RuntimeError(
            "An incomplete pinned media runtime already exists and will not be "
            f"overwritten: {install_root}"
        )
    archive = _download(
        runtime_root,
        url=bundle.archive_url,
        sha256=bundle.archive_sha256,
    )
    staging = runtime_root / "deps" / f"staging-{bundle.provider}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        _extract_archive(archive, staging, bundle.archive_format)
        staged_bundle = staging / bundle.archive_root
        if not staged_bundle.is_dir():
            raise RuntimeError(
                f"Pinned {bundle.provider} archive is missing {bundle.archive_root}"
            )
        if contract.target.operating_system == "windows":
            _prepare_windows_preview_repository(staged_bundle)
        install_root.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(install_root)
    except BaseException as error:
        archived = _archive_failure(runtime_root, staging, "bundle-staging-failed")
        if archived is not None:
            error.add_note(f"Failed runtime staging archived at {archived}")
        raise


def _prepare_chromium(runtime_root: Path, contract: RuntimeContract) -> None:
    browser = contract.playwright
    install_root = runtime_root / "deps" / f"chromium-{browser.browser_version}"
    executable = contract.chromium_directory(runtime_root) / browser.executable
    archive = _download(
        runtime_root,
        url=browser.archive_url,
        sha256=browser.archive_sha256,
    )
    if executable.is_file():
        _restore_zip_permissions(archive, install_root)
        _make_executable(executable)
        return
    if install_root.exists():
        raise RuntimeError(
            "An incomplete pinned Chromium runtime already exists and will not be "
            f"overwritten: {install_root}"
        )
    staging = runtime_root / "deps" / f"staging-chromium-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        _extract_archive(archive, staging, "zip")
        staged_executable = staging / browser.archive_root / browser.executable
        if not staged_executable.is_file():
            raise RuntimeError(
                f"Pinned Chromium archive is missing {browser.executable}"
            )
        _make_executable(staged_executable)
        install_root.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(install_root)
    except BaseException as error:
        archived = _archive_failure(runtime_root, staging, "chromium-staging-failed")
        if archived is not None:
            error.add_note(f"Failed Chromium staging archived at {archived}")
        raise


def prepare_runtime(
    runtime_root: Path,
    *,
    target: PlatformTarget | None = None,
) -> RuntimePaths:
    selected = target or PlatformTarget.current()
    contract = load_runtime_contract(target=selected)
    root = runtime_root.expanduser().resolve()
    (root / "deps").mkdir(parents=True, exist_ok=True)
    _prepare_bundle(root, contract)
    _prepare_chromium(root, contract)
    paths = RuntimePaths.from_contract(contract, runtime_root=root)
    for executable in (paths.ffmpeg, paths.ffprobe, paths.melt, paths.chromium):
        _make_executable(executable)
    _require_runtime_layout(paths)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the checksum-pinned MediaFlow runtime",
    )
    parser.add_argument("--runtime-root", type=Path, default=runtime_directory())
    args = parser.parse_args(argv)
    paths = prepare_runtime(args.runtime_root)
    print(
        json.dumps(
            {
                "target": paths.target.key,
                "runtime_root": str(paths.runtime_dir),
                "ffmpeg": str(paths.ffmpeg),
                "ffprobe": str(paths.ffprobe),
                "melt": str(paths.melt),
                "chromium": str(paths.chromium),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
