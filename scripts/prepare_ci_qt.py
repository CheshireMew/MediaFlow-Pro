from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import py7zr
from aqt.archives import TargetConfig
from aqt.updater import Updater

from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.runtime_contract import PlatformTarget

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_QT_FILES = (
    "lib/cmake/Qt6/Qt6Config.cmake",
    "lib/cmake/Qt6Quick/Qt6QuickConfig.cmake",
    "lib/cmake/Qt6QuickPrivate/Qt6QuickPrivateConfig.cmake",
)


def _load_qt_contract(
    path: Path,
    target: PlatformTarget,
) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("schema_version") != 2:
        raise RuntimeError(
            f"Unsupported runtime lock schema: {contract.get('schema_version')!r}"
        )
    target_contract = contract["targets"].get(target.key)
    if not isinstance(target_contract, dict):
        raise RuntimeError(f"Runtime lock does not declare {target.key}")
    qt = {
        **target_contract["qt"],
        "archives": target_contract.get("qt_archives"),
    }
    archives = qt.get("archives")
    if not isinstance(archives, list) or not archives:
        raise RuntimeError("The Qt runtime contract must declare pinned archives")
    required_archives = {"qtbase", "qtdeclarative"}
    if target.operating_system == "linux":
        required_archives.add("icu")
    if {item.get("name") for item in archives} != required_archives:
        expected = ", ".join(sorted(required_archives))
        raise RuntimeError(f"The {target.key} Qt SDK must contain exactly {expected}")
    for item in archives:
        checksum = item.get("sha256")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise RuntimeError(f"Invalid SHA-256 for Qt archive {item.get('name')!r}")
    return qt


def _download_archive(item: dict[str, str], download_root: Path) -> Path:
    url = item["url"]
    destination = download_root / Path(url).name
    expected = item["sha256"].lower()
    if destination.is_file():
        actual = sha256_file(destination)
        if actual != expected:
            raise RuntimeError(
                f"Pinned Qt archive checksum mismatch: {destination} ({actual})"
            )
        return destination

    temporary = download_root / f"download-{uuid.uuid4().hex}.7z"
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "MediaFlow-CI/1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open(
            "wb"
        ) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = sha256_file(temporary)
        if actual != expected:
            raise RuntimeError(
                f"Downloaded Qt archive checksum mismatch: {temporary} ({actual})"
            )
        temporary.replace(destination)
    except BaseException as error:
        if temporary.exists():
            archive = download_root.parent / "archive" / "qt-downloads"
            archive.mkdir(parents=True, exist_ok=True)
            failed = archive / f"download-failed-{uuid.uuid4().hex}.7z"
            temporary.replace(failed)
            error.add_note(f"Failed Qt download archived at {failed}")
        raise
    return destination


def prepare_qt(
    qt_root: Path,
    contract_path: Path,
    *,
    target: PlatformTarget | None = None,
) -> Path:
    selected = target or PlatformTarget.current()
    qt = _load_qt_contract(contract_path, selected)
    install_root = (qt_root / qt["install_directory"]).resolve()
    if all((install_root / relative).is_file() for relative in REQUIRED_QT_FILES):
        return install_root
    if install_root.exists():
        raise RuntimeError(
            "An incomplete pinned Qt SDK already exists and will not be overwritten: "
            f"{install_root}"
        )

    download_root = qt_root / "downloads"
    download_root.mkdir(parents=True, exist_ok=True)
    staging_root = qt_root / f"install-{uuid.uuid4().hex}"
    staging_install = staging_root / qt["install_directory"]
    staging_install.mkdir(parents=True)
    try:
        for item in qt["archives"]:
            archive = _download_archive(item, download_root)
            with py7zr.SevenZipFile(archive, mode="r") as source:
                source.extractall(path=staging_install)

        Updater.update(
            TargetConfig(
                version=qt["version"],
                target="desktop",
                arch=qt["architecture"],
                os_name={
                    "windows": "windows",
                    "linux": "linux",
                    "macos": "mac",
                }[selected.operating_system],
            ),
            staging_root,
            None,
        )
        missing = [
            relative
            for relative in REQUIRED_QT_FILES
            if not (staging_install / relative).is_file()
        ]
        if missing:
            raise RuntimeError(
                f"Pinned Qt SDK is incomplete: {', '.join(missing)}"
            )
        install_root.parent.mkdir(parents=True, exist_ok=True)
        staging_install.replace(install_root)
        archive = qt_root / "archive" / "qt-install-metadata"
        archive.mkdir(parents=True, exist_ok=True)
        staging_root.replace(archive / staging_root.name)
    except BaseException as error:
        if staging_root.exists():
            archive = qt_root / "archive"
            archive.mkdir(parents=True, exist_ok=True)
            failed = archive / f"qt-install-failed-{uuid.uuid4().hex}"
            staging_root.replace(failed)
            error.add_note(f"Failed Qt staging archived at {failed}")
        raise
    return install_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qt-root", required=True, type=Path)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "runtime.lock.json",
    )
    args = parser.parse_args(argv)
    install_root = prepare_qt(args.qt_root.resolve(), args.contract.resolve())
    print(install_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
