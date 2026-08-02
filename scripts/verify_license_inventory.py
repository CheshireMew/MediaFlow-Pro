# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from importlib.metadata import metadata, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from scripts.run_artifacts import verification_run

LOCK_FILE = ROOT / "requirements.lock"
RUNTIME_LOCK_FILE = ROOT / "runtime.lock.json"
PYTHON_COMPONENTS = {
    "aqtinstall": {"MIT", "MIT License"},
    "PySide6": {"LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"},
    "pydantic": {"MIT"},
    "yt-dlp": {"Unlicense"},
    "faster-whisper": {"MIT"},
    "openai": {"Apache-2.0"},
    "json-repair": {"MIT"},
    "playwright": {"Apache-2.0"},
    "opencv-python-headless": {"Apache 2.0", "Apache-2.0"},
    "av": {"BSD-3-Clause"},
    "ctranslate2": {"MIT"},
    "huggingface-hub": {"Apache-2.0"},
}
LOCKED_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\\\s]+)")


def canonical_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        match = LOCKED_REQUIREMENT.match(line)
        if match is not None:
            versions[canonical_package_name(match.group(1))] = match.group(2)
    return versions


def package_row(name: str, lock: dict[str, str]) -> dict[str, object]:
    expected_licenses = PYTHON_COMPONENTS[name]
    expected_version = lock.get(canonical_package_name(name))
    if expected_version is None:
        raise RuntimeError(f"{name} is missing from {LOCK_FILE}")
    package_metadata = metadata(name)
    license_value = package_metadata.get("License-Expression") or package_metadata.get("License") or ""
    actual_version = version(name)
    return {
        "name": name,
        "expected_version": expected_version,
        "actual_version": actual_version,
        "license": license_value,
        "passed": actual_version == expected_version and license_value in expected_licenses,
    }


def command_first_line(executable: Path, *arguments: str) -> str:
    result = subprocess.run(
        [str(executable), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (result.stdout or result.stderr).splitlines()[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    arguments = parser.parse_args(argv)
    with verification_run(
        "license-inventory",
        explicit_parent=arguments.output_dir,
    ) as run_dir:
        return verify(arguments, run_dir)


def verify(arguments: argparse.Namespace, run_dir: Path) -> int:
    lock = locked_versions()
    packages = [package_row(name, lock) for name in PYTHON_COMPONENTS]
    gpl_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    ofl_text = (ROOT / "mediaflow/resources/fonts/OFL.txt").read_text(encoding="utf-8")
    external: dict[str, object]
    if arguments.python_only:
        external = {"checked": False, "reason": "python-only"}
    else:
        runtime_contract = json.loads(
            RUNTIME_LOCK_FILE.read_text(encoding="utf-8")
        )["windows"]["shotcut"]
        paths = RuntimePaths.discover()
        if paths.melt is None:
            raise RuntimeError("MLT is required for the full license inventory")
        ffmpeg = paths.ffmpeg
        melt = paths.melt
        ffmpeg_configuration = subprocess.run(
            [str(ffmpeg), "-version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
        external = {
            "checked": True,
            "mlt": command_first_line(melt, "-version"),
            "ffmpeg": command_first_line(ffmpeg, "-version"),
            "expected_mlt_version": runtime_contract["melt_version"],
            "expected_ffmpeg_version": runtime_contract["ffmpeg_version"],
            "ffmpeg_gpl_v3_configuration": "--enable-gpl" in ffmpeg_configuration
            and "--enable-version3" in ffmpeg_configuration,
        }
    report = {
        "project_license": "GPL-3.0-only",
        "dependency_lock": str(LOCK_FILE),
        "runtime_lock": str(RUNTIME_LOCK_FILE),
        "gpl_text_present": "GNU GENERAL PUBLIC LICENSE" in gpl_text and "Version 3" in gpl_text,
        "font_ofl_text_present": "SIL OPEN FONT LICENSE" in ofl_text and "Version 1.1" in ofl_text,
        "python_components": packages,
        "external_runtime": external,
    }
    base_passed = (
        report["gpl_text_present"]
        and report["font_ofl_text_present"]
        and all(row["passed"] for row in packages)
    )
    runtime_passed = arguments.python_only or (
        str(external["mlt"])
        == f"melt.exe {external['expected_mlt_version']}"
        and str(external["ffmpeg"]).startswith(
            f"ffmpeg version {external['expected_ffmpeg_version']} "
        )
        and bool(external["ffmpeg_gpl_v3_configuration"])
    )
    report["passed"] = base_passed and runtime_passed
    report_path = run_dir / "license-inventory-report.json"
    atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
    print(report_path)
    if not report["passed"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
