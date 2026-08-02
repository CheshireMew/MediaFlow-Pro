from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mediaflow.infrastructure import runtime_paths
from mediaflow.infrastructure.runtime_contract import load_runtime_contract

ROOT = Path(__file__).resolve().parents[3]
RUNTIME_LOCK = ROOT / "runtime.lock.json"
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"


def test_windows_media_runtime_has_one_checksum_pinned_contract() -> None:
    contract = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
    assert set(contract) == {"schema_version", "windows"}
    assert contract["schema_version"] == 1
    windows = contract["windows"]
    assert set(windows) == {"shotcut", "qt"}
    shotcut = windows["shotcut"]
    assert set(shotcut) == {
        "version",
        "archive_url",
        "archive_sha256",
        "archive_root",
        "melt_version",
        "ffmpeg_version",
    }
    assert shotcut["version"] in shotcut["archive_url"]
    assert shotcut["archive_url"].startswith(
        "https://github.com/mltframework/shotcut/releases/download/"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", shotcut["archive_sha256"])
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"PySide6=={windows["qt"]["version"]}"' in pyproject
    assert '"aqtinstall==3.3.0"' in pyproject
    requirements = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert "aqtinstall==3.3.0" in requirements


def test_isolated_cache_root_reuses_the_default_pinned_media_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_runtime = tmp_path / "isolated" / "runtime"
    default_runtime = tmp_path / "reviewed" / "runtime"
    contract = load_runtime_contract()
    shotcut = contract.shotcut_directory(default_runtime.parent)
    shotcut.mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "melt.exe"):
        (shotcut / name).write_bytes(b"reviewed runtime")

    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(isolated_runtime))
    monkeypatch.delenv("MEDIAFLOW_FFMPEG", raising=False)
    monkeypatch.delenv("MEDIAFLOW_FFPROBE", raising=False)
    monkeypatch.delenv("MEDIAFLOW_MELT", raising=False)
    monkeypatch.setattr(runtime_paths, "DEFAULT_RUNTIME_DIRECTORY", default_runtime)
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda _command: None)

    discovered = runtime_paths.RuntimePathDiscovery.discover()

    assert discovered.runtime_dir == isolated_runtime.resolve()
    assert discovered.ffmpeg == (shotcut / "ffmpeg.exe").resolve()
    assert discovered.ffprobe == (shotcut / "ffprobe.exe").resolve()
    assert discovered.melt == (shotcut / "melt.exe").resolve()


def test_quality_workflow_provisions_and_exercises_every_media_runtime() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required_steps = (
        "scripts/prepare_ci_runtime.ps1",
        "python -m playwright install chromium",
        "scripts/build_native.ps1",
        "scripts/verify_development_runtime.py",
        "scripts/verify_display_capabilities.py",
        "test_real_draw_element_failure_requires_clean_screenshot_retry",
        "tests/v2/integration/test_native_preview.py",
        "python -m scripts.verify_real_user_chain",
    )
    assert all(step in workflow for step in required_steps)
    assert "--python-only" not in workflow
    assert not re.search(
        r"^\s*uses:\s*[^#\s]+@v\d+\s*(?:#.*)?$",
        workflow,
        flags=re.MULTILINE,
    )
