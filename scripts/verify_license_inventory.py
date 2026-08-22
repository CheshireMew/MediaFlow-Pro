# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from importlib.metadata import metadata, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_contract import (
    load_runtime_contract,
    reported_version_at_least,
)
from mediaflow.infrastructure.subprocess_runner import run_cancellable
from scripts.run_artifacts import verification_run

LOCK_FILE = ROOT / "requirements.lock"
RUNTIME_LOCK_FILE = ROOT / "runtime.lock.json"
RUNTIME_COMPONENT_LOCK_FILE = ROOT / "mediaflow/resources/runtime-components.lock.json"
THIRD_PARTY_NOTICES_FILE = ROOT / "THIRD_PARTY_NOTICES.md"
SPEAKER_DIARIZATION_SETUP_FILE = ROOT / "scripts/setup_speaker_diarization.ps1"
PYTHON_COMPONENTS = {
    "aqtinstall": {"MIT", "MIT License"},
    "PySide6": {"LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"},
    "pydantic": {"MIT"},
    "aiohttp": {"Apache-2.0 AND MIT"},
    "psutil": {"BSD-3-Clause"},
    "mcp": {"MIT"},
    "mcp-types": {"MIT"},
    "yt-dlp": {"Unlicense"},
    "faster-whisper": {"MIT"},
    "openai": {"Apache-2.0"},
    "json-repair": {"MIT"},
    "playwright": {"Apache-2.0"},
    "opencv-python-headless": {"Apache 2.0", "Apache-2.0"},
    "av": {"BSD-3-Clause"},
    "ctranslate2": {"MIT"},
    "huggingface-hub": {"Apache-2.0"},
    "pyinstaller": {
        "GPLv2-or-later with a special exception which allows to use PyInstaller to "
        "build and distribute non-free programs (including commercial ones)"
    },
}
if platform.system() == "Windows":
    PYTHON_COMPONENTS["pywin32"] = {"PSF"}
LOCKED_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\\\s]+)")

DIARIZATION_NOTICE_REQUIREMENTS = {
    "PyTorch": ("torch==2.11.0", "BSD-3-Clause"),
    "TorchAudio": ("torchaudio==2.11.0", "BSD-2-Clause"),
    "pyannote.audio": ("pyannote.audio==4.0.7", "MIT"),
    "pyannote Community-1": (
        "pyannote/speaker-diarization-community-1",
        "CC-BY-4.0",
    ),
}


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


def on_demand_runtime_rows(notices: str) -> list[dict[str, object]]:
    document = json.loads(RUNTIME_COMPONENT_LOCK_FILE.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 3:
        raise RuntimeError("Unsupported runtime component license contract")
    records = document.get("components")
    if not isinstance(records, list) or not records:
        raise RuntimeError("Runtime component license contract has no components")
    rows: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("Runtime component license record must be an object")
        license_value = str(record.get("license") or "")
        distribution = str(record.get("distribution") or "")
        bundle_allowed = record.get("bundle_allowed") is True
        values = (
            str(record.get("display_name") or ""),
            str(record.get("version") or ""),
            str(record.get("homepage") or ""),
        )
        documented = all(value and value in notices for value in values)
        policy_passed = distribution in {"upstream-download-only", "redistributable"}
        if license_value == "NOASSERTION":
            policy_passed = (
                policy_passed
                and distribution == "upstream-download-only"
                and not bundle_allowed
                and "Do not redistribute" in str(record.get("license_notes") or "")
            )
        rows.append(
            {
                "id": str(record.get("id") or ""),
                "version": str(record.get("version") or ""),
                "license": license_value,
                "distribution": distribution,
                "bundle_allowed": bundle_allowed,
                "documented": documented,
                "passed": documented and policy_passed,
            }
        )
    return rows


def diarization_runtime_rows(notices: str) -> list[dict[str, object]]:
    setup = SPEAKER_DIARIZATION_SETUP_FILE.read_text(encoding="utf-8")
    runtime_contract = setup + (ROOT / "mediaflow/domain/settings.py").read_text(encoding="utf-8")
    rows: list[dict[str, object]] = []
    for name, (setup_token, license_value) in DIARIZATION_NOTICE_REQUIREMENTS.items():
        rows.append(
            {
                "name": name,
                "setup_token": setup_token,
                "license": license_value,
                "passed": (
                    setup_token in runtime_contract
                    and name in notices
                    and license_value in notices
                ),
            }
        )
    gated_terms_documented = (
        "accept its Hugging Face conditions" in notices
        and "their own access token" in notices
        and "CC-BY-4.0 attribution requirements" in notices
    )
    rows.append(
        {
            "name": "Community-1 access terms",
            "passed": gated_terms_documented,
        }
    )
    return rows


def command_first_line(executable: Path, *arguments: str) -> str:
    result = run_cancellable(
        [str(executable), *arguments],
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
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
    notices = THIRD_PARTY_NOTICES_FILE.read_text(encoding="utf-8")
    on_demand_runtimes = on_demand_runtime_rows(notices)
    diarization_runtimes = diarization_runtime_rows(notices)
    gpl_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    ofl_text = (ROOT / "mediaflow/resources/fonts/OFL.txt").read_text(encoding="utf-8")
    external: dict[str, object]
    if arguments.python_only:
        external = {"checked": False, "reason": "python-only"}
    else:
        runtime_contract = load_runtime_contract(RUNTIME_LOCK_FILE)
        paths = RuntimeContext.discover().paths
        if paths.melt is None:
            raise RuntimeError("MLT is required for the full license inventory")
        ffmpeg = paths.ffmpeg
        melt = paths.melt
        ffmpeg_result = run_cancellable(
            [str(ffmpeg), "-version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        ffmpeg_configuration = ffmpeg_result.stdout or ffmpeg_result.stderr
        ffmpeg_line = ffmpeg_configuration.splitlines()[0]
        melt_line = command_first_line(melt, "-version")
        ffmpeg_version_matches = (
            ffmpeg_line.startswith(f"ffmpeg version {runtime_contract.ffmpeg_version} ")
            if runtime_contract.ffmpeg_version_match == "exact"
            else reported_version_at_least(
                ffmpeg_line,
                runtime_contract.ffmpeg_version,
            )
        )
        melt_version_matches = (
            runtime_contract.melt_version in melt_line
            if runtime_contract.melt_version_match == "exact"
            else reported_version_at_least(
                melt_line,
                runtime_contract.melt_version,
            )
        )
        reviewed_gpl_v3_required = runtime_contract.reviewed_bundle is not None
        ffmpeg_gpl_v3_configuration = (
            "--enable-gpl" in ffmpeg_configuration
            and "--enable-version3" in ffmpeg_configuration
        )
        external = {
            "checked": True,
            "target": runtime_contract.target.key,
            "mlt": melt_line,
            "ffmpeg": ffmpeg_line,
            "expected_mlt_version": runtime_contract.melt_version,
            "expected_mlt_version_match": runtime_contract.melt_version_match,
            "expected_ffmpeg_version": runtime_contract.ffmpeg_version,
            "expected_ffmpeg_version_match": runtime_contract.ffmpeg_version_match,
            "mlt_version_matches": melt_version_matches,
            "ffmpeg_version_matches": ffmpeg_version_matches,
            "reviewed_gpl_v3_required": reviewed_gpl_v3_required,
            "ffmpeg_gpl_v3_configuration": ffmpeg_gpl_v3_configuration,
        }
    report = {
        "project_license": "GPL-3.0-only",
        "dependency_lock": str(LOCK_FILE),
        "runtime_lock": str(RUNTIME_LOCK_FILE),
        "gpl_text_present": "GNU GENERAL PUBLIC LICENSE" in gpl_text and "Version 3" in gpl_text,
        "font_ofl_text_present": "SIL OPEN FONT LICENSE" in ofl_text and "Version 1.1" in ofl_text,
        "python_components": packages,
        "on_demand_runtime_components": on_demand_runtimes,
        "on_demand_diarization_runtime": diarization_runtimes,
        "external_runtime": external,
    }
    base_passed = (
        report["gpl_text_present"]
        and report["font_ofl_text_present"]
        and all(row["passed"] for row in packages)
        and all(row["passed"] for row in on_demand_runtimes)
        and all(row["passed"] for row in diarization_runtimes)
    )
    runtime_passed = arguments.python_only or (
        bool(external["mlt_version_matches"])
        and bool(external["ffmpeg_version_matches"])
        and (
            not bool(external["reviewed_gpl_v3_required"])
            or bool(external["ffmpeg_gpl_v3_configuration"])
        )
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
