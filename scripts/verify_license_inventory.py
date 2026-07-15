from __future__ import annotations

import json
import subprocess
from datetime import datetime
from importlib.metadata import metadata, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = Path("D:/Tools/MediaFlow/test-runs")
PYTHON_COMPONENTS = {
    "PySide6": ("6.11.1", {"LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"}),
    "pydantic": ("2.13.4", {"MIT"}),
    "yt-dlp": ("2026.3.17", {"Unlicense"}),
    "faster-whisper": ("1.2.1", {"MIT"}),
    "openai": ("2.45.0", {"Apache-2.0"}),
    "json-repair": ("0.61.4", {"MIT"}),
    "av": ("18.0.0", {"BSD-3-Clause"}),
    "ctranslate2": ("4.8.1", {"MIT"}),
    "huggingface-hub": ("1.23.0", {"Apache-2.0"}),
}


def package_row(name: str) -> dict[str, object]:
    expected_version, expected_licenses = PYTHON_COMPONENTS[name]
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


def main() -> int:
    run_dir = RUN_ROOT / f"license-inventory-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=False)
    packages = [package_row(name) for name in PYTHON_COMPONENTS]
    gpl_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    ofl_text = (ROOT / "mediaflow/resources/fonts/OFL.txt").read_text(encoding="utf-8")
    ffmpeg = Path("D:/Tools/MediaFlow/deps/shotcut-26.6.25/Shotcut/ffmpeg.exe")
    melt = Path("D:/Tools/MediaFlow/deps/shotcut-26.6.25/Shotcut/melt.exe")
    ffmpeg_configuration = subprocess.run(
        [str(ffmpeg), "-version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    external = {
        "mlt": command_first_line(melt, "-version"),
        "ffmpeg": command_first_line(ffmpeg, "-version"),
        "ffmpeg_gpl_v3_configuration": "--enable-gpl" in ffmpeg_configuration
        and "--enable-version3" in ffmpeg_configuration,
    }
    report = {
        "project_license": "GPL-3.0-only",
        "gpl_text_present": "GNU GENERAL PUBLIC LICENSE" in gpl_text and "Version 3" in gpl_text,
        "font_ofl_text_present": "SIL OPEN FONT LICENSE" in ofl_text and "Version 1.1" in ofl_text,
        "python_components": packages,
        "external_runtime": external,
    }
    report["passed"] = (
        report["gpl_text_present"]
        and report["font_ofl_text_present"]
        and all(row["passed"] for row in packages)
        and external["mlt"].startswith("melt.exe 7.40.0")
        and external["ffmpeg_gpl_v3_configuration"]
    )
    report_path = run_dir / "license-inventory-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    if not report["passed"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
