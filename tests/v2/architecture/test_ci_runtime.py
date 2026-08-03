from __future__ import annotations

import json
import re
from pathlib import Path

import py7zr
import pytest

from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure import runtime_paths
from mediaflow.infrastructure.runtime_contract import load_runtime_contract
from scripts import prepare_ci_qt

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
    qt = windows["qt"]
    assert qt["install_directory"] == f'{qt["version"]}/msvc2022_64'
    assert [archive["name"] for archive in qt["archives"]] == [
        "qtbase",
        "qtdeclarative",
    ]
    for archive in qt["archives"]:
        assert archive["url"].startswith(
            "https://download.qt.io/online/qtsdkrepository/"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", archive["sha256"])
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"PySide6=={windows["qt"]["version"]}"' in pyproject
    assert '"aqtinstall==3.3.0"' in pyproject
    requirements = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert "aqtinstall==3.3.0" in requirements


def test_isolated_runtime_reuses_the_environment_owned_pinned_toolchain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_runtime = tmp_path / "isolated" / "runtime"
    development_root = tmp_path / "reviewed"
    contract = load_runtime_contract()
    shotcut = contract.shotcut_directory(development_root)
    shotcut.mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "melt.exe"):
        (shotcut / name).write_bytes(b"reviewed runtime")

    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(isolated_runtime))
    monkeypatch.setenv("MEDIAFLOW_DEV_ROOT", str(development_root))
    monkeypatch.delenv("MEDIAFLOW_FFMPEG", raising=False)
    monkeypatch.delenv("MEDIAFLOW_FFPROBE", raising=False)
    monkeypatch.delenv("MEDIAFLOW_MELT", raising=False)
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
        "scripts/prepare_ci_qt.py",
        "python -m playwright install chromium",
        "scripts/build_native.ps1",
        "scripts/verify_development_runtime.py",
        "scripts/verify_display_capabilities.py",
        "test_drag_import_placement_snap_tracks_and_first_video_profile",
        "tests/v2/desktop/test_web_editor.py",
        "test_real_draw_element_failure_requires_clean_screenshot_retry",
        "tests/v2/integration/test_native_preview.py",
        "scripts.verify_web_render_performance",
        "scripts.verify_reference_comparison_chain",
    )
    assert all(step in workflow for step in required_steps)
    assert all(
        variable in workflow
        for variable in (
            "MEDIAFLOW_DEV_ROOT",
            "MEDIAFLOW_PROJECT_ROOT",
            "MEDIAFLOW_MEDIA_ROOT",
            "MEDIAFLOW_TEST_ROOT",
            "MEDIAFLOW_TEST_FIXTURE_ROOT",
            "PYTHONUTF8",
        )
    )
    assert "--python-only" not in workflow
    assert "if: ${{ vars.MEDIAFLOW_RUN_ONLINE_E2E == 'true' }}" in workflow
    assert not re.search(
        r"^\s*uses:\s*[^#\s]+@v\d+\s*(?:#.*)?$",
        workflow,
        flags=re.MULTILINE,
    )


def test_web_desktop_scenarios_run_in_separate_qtwebengine_processes() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    first_scenario = (
        "tests/v2/desktop/test_web_editor.py::"
        "test_unified_import_opens_the_v5_package_through_local_preview_server"
    )
    second_scenario = (
        "tests/v2/desktop/test_web_editor.py::"
        "test_real_dom_drag_crosses_webchannel_persists_and_is_read_back_by_page"
    )

    assert workflow.count("python -m pytest tests/v2/desktop/test_web_editor.py::") == 2
    assert first_scenario in workflow
    assert second_scenario in workflow
    assert "python -m pytest tests/v2/desktop/test_web_editor.py\n" not in workflow


def test_qt_preparation_publishes_only_a_complete_checksum_verified_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qt_root = tmp_path / "Qt"
    downloads = qt_root / "downloads"
    downloads.mkdir(parents=True)
    archive_files = {
        "qtbase": ("lib/cmake/Qt6/Qt6Config.cmake",),
        "qtdeclarative": (
            "lib/cmake/Qt6Quick/Qt6QuickConfig.cmake",
            "lib/cmake/Qt6QuickPrivate/Qt6QuickPrivateConfig.cmake",
        ),
    }
    archives = []
    for name, relative_files in archive_files.items():
        source_root = tmp_path / f"{name}-source"
        archive = downloads / f"{name}.7z"
        with py7zr.SevenZipFile(archive, mode="w") as output:
            for relative in relative_files:
                source = source_root / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(name, encoding="utf-8")
                output.write(source, arcname=relative)
        archives.append(
            {
                "name": name,
                "url": f"https://invalid.example/{archive.name}",
                "sha256": sha256_file(archive),
            }
        )
    contract = tmp_path / "runtime.lock.json"
    contract.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "windows": {
                    "qt": {
                        "version": "6.11.1",
                        "architecture": "win64_msvc2022_64",
                        "install_directory": "6.11.1/msvc2022_64",
                        "archives": archives,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        prepare_ci_qt.Updater,
        "update",
        lambda *_args, **_kwargs: None,
    )

    installed = prepare_ci_qt.prepare_qt(qt_root, contract)

    assert installed == (qt_root / "6.11.1/msvc2022_64").resolve()
    assert all(
        (installed / relative).is_file()
        for relative in prepare_ci_qt.REQUIRED_QT_FILES
    )
    assert not list(qt_root.glob("install-*"))
