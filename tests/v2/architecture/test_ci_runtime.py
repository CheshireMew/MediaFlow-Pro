from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import py7zr
import pytest

from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.runtime_contract import PlatformTarget, load_runtime_contract
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from scripts import prepare_ci_qt, prepare_runtime

ROOT = Path(__file__).resolve().parents[3]
RUNTIME_LOCK = ROOT / "runtime.lock.json"
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"


def test_three_platform_media_runtime_has_one_versioned_contract() -> None:
    contract = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
    assert set(contract) == {"schema_version", "targets"}
    assert contract["schema_version"] == 2
    assert set(contract["targets"]) == {
        "windows-x86_64",
        "linux-x86_64",
        "macos-arm64",
    }
    for key, target in contract["targets"].items():
        assert key == f'{target["operating_system"]}-{target["architecture"]}'
        assert set(target) >= {
            "operating_system",
            "architecture",
            "minimum_release",
            "ffmpeg",
            "mlt",
            "qt",
            "reviewed_bundle",
            "layout",
            "playwright",
            "qt_archives",
        }
        assert target["ffmpeg"]["version"] == "n8.1.2"
        assert target["ffmpeg"]["version_match"] == "exact"
        assert target["mlt"]["version"] == "7.40.0"
        assert target["mlt"]["version_match"] == "exact"
        assert target["qt"]["version"] == "6.11.1"
        assert target["reviewed_bundle"]["provider"] == "shotcut"
        assert target["reviewed_bundle"]["version"] == "26.6.25"
        assert target["reviewed_bundle"]["archive_format"] in {"zip", "txz", "dmg"}
        assert target["layout"]["mlt_preview_repository"].endswith("mlt-preview-v2")
        assert target["layout"]["mlt_preview_repository"] != target["layout"]["mlt_repository"]
        assert re.fullmatch(
            r"[0-9a-f]{64}", target["reviewed_bundle"]["archive_sha256"]
        )
        assert target["playwright"]["version"] == "1.61.0"
        assert target["playwright"]["chromium_revision"] == "1228"
        assert target["playwright"]["browser_version"] == "149.0.7827.55"
        assert re.fullmatch(r"[0-9a-f]{64}", target["playwright"]["archive_sha256"])
        expected_qt_archives = ["qtbase", "qtdeclarative"]
        if target["operating_system"] == "linux":
            expected_qt_archives.append("icu")
        assert [archive["name"] for archive in target["qt_archives"]] == (
            expected_qt_archives
        )
        assert all(
            re.fullmatch(r"[0-9a-f]{64}", archive["sha256"])
            for archive in target["qt_archives"]
        )

    windows = contract["targets"]["windows-x86_64"]
    shotcut = windows["reviewed_bundle"]
    assert set(shotcut) == {
        "provider",
        "version",
        "archive_url",
        "archive_sha256",
        "archive_format",
        "archive_root",
    }
    assert shotcut["provider"] == "shotcut"
    assert shotcut["version"] in shotcut["archive_url"]
    assert shotcut["archive_url"].startswith(
        "https://github.com/mltframework/shotcut/releases/download/"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", shotcut["archive_sha256"])
    qt = windows["qt"]
    assert qt["toolchain"] == "msvc2022_64"
    assert [archive["name"] for archive in windows["qt_archives"]] == [
        "qtbase",
        "qtdeclarative",
    ]
    for archive in windows["qt_archives"]:
        assert archive["url"].startswith(
            "https://download.qt.io/online/qtsdkrepository/"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", archive["sha256"])
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"PySide6=={windows["qt"]["version"]}"' in pyproject
    assert '"aqtinstall==3.3.0"' in pyproject
    assert '"macholib==1.16.4"' in pyproject
    requirements = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert "aqtinstall==3.3.0" in requirements
    assert "macholib==1.16.4 \\\n    --hash=sha256:" in requirements


@pytest.mark.parametrize(
    "target",
    (
        PlatformTarget("windows", "x86_64"),
        PlatformTarget("linux", "x86_64"),
        PlatformTarget("macos", "arm64"),
    ),
)
def test_each_target_resolves_only_its_pinned_layout_from_one_runtime_root(
    tmp_path: Path,
    target: PlatformTarget,
) -> None:
    runtime = tmp_path / target.key / "runtime"
    contract = load_runtime_contract(target=target)

    paths = RuntimePaths.from_contract(contract, runtime_root=runtime)
    bundle = contract.reviewed_bundle_directory(runtime)

    assert paths.target == target
    assert paths.runtime_dir == runtime.resolve()
    assert paths.ffmpeg == (bundle / contract.layout.ffmpeg).resolve()
    assert paths.ffprobe == (bundle / contract.layout.ffprobe).resolve()
    assert paths.melt == (bundle / contract.layout.melt).resolve()
    assert paths.mlt_library == (bundle / contract.layout.mlt_library).resolve()
    assert paths.mlt_repository == (bundle / contract.layout.mlt_repository).resolve()
    assert paths.mlt_preview_repository == (
        bundle / contract.layout.mlt_preview_repository
    ).resolve()
    assert paths.mlt_data == (bundle / contract.layout.mlt_data).resolve()
    assert paths.chromium == (
        contract.chromium_directory(runtime) / contract.playwright.executable
    ).resolve()
    assert paths.native_qml == (runtime / contract.layout.native_qml).resolve()
    assert paths.render_identity is not None
    assert paths.render_identity.platform == target.operating_system
    assert paths.render_identity.architecture == target.architecture
    assert paths.render_identity.runtime_digest == contract.digest


def test_runtime_root_is_the_only_configurable_toolchain_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_runtime = tmp_path / "isolated" / "runtime"
    development_root = tmp_path / "unrelated-development-root"
    contract = load_runtime_contract()

    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(isolated_runtime))
    monkeypatch.setenv("MEDIAFLOW_DEV_ROOT", str(development_root))

    paths = RuntimePaths.from_contract(contract)
    bundle = contract.reviewed_bundle_directory(isolated_runtime)

    assert paths.runtime_dir == isolated_runtime.resolve()
    assert paths.ffmpeg == (bundle / contract.layout.ffmpeg).resolve()
    source = (ROOT / "mediaflow" / "infrastructure" / "runtime_paths.py").read_text(
        encoding="utf-8"
    )
    assert "shutil.which" not in source
    assert "Program Files" not in source
    assert "MEDIAFLOW_FFMPEG" not in source
    assert "MEDIAFLOW_CHROMIUM" not in source


def test_native_preview_consumes_explicit_runtime_paths_without_layout_guesses() -> None:
    source = (ROOT / "mediaflow" / "desktop" / "native" / "MltRuntime.cpp").read_text(
        encoding="utf-8"
    )
    assert 'qEnvironmentVariable("MLT_REPOSITORY")' not in source
    assert 'qEnvironmentVariable("MLT_DATA")' not in source
    assert "libmlt-7.dll" not in source
    assert "libmlt-7.so" not in source
    assert "libmlt-7.dylib" not in source
    assert "lib/mlt-preview" not in source
    assert "share/mlt" not in source
    preview_source = (
        ROOT / "mediaflow" / "desktop" / "native" / "MltPreviewItem.cpp"
    ).read_text(encoding="utf-8")
    assert "QFileInfo mltLibraryInfo(mltLibrary)" in preview_source
    assert "mltLibraryInfo.absolutePath()" in preview_source
    assert "QLibrary::ExportExternalSymbolsHint" in preview_source
    assert "#ifdef Q_OS_LINUX" in preview_source
    runtime_preparation = (ROOT / "scripts" / "prepare_runtime.py").read_text(
        encoding="utf-8"
    )
    assert '"install_name_tool", "-add_rpath"' in runtime_preparation
    assert '"@loader_path/../../Frameworks"' in runtime_preparation
    assert '["codesign", "--force", "--sign", "-"' in runtime_preparation
    assert "_prepare_preview_repository(bundle_root, contract)" in runtime_preparation
    assert "_prepare_preview_repository(staged_bundle, contract)" in runtime_preparation
    assert runtime_preparation.count("_prepare_macos_runtime_rpaths(") == 3


@pytest.mark.parametrize(
    ("operating_system", "suffix"),
    (("windows", ".dll"), ("linux", ".so"), ("macos", ".so")),
)
def test_preview_repository_omits_process_conflicting_plugins(
    tmp_path: Path,
    operating_system: str,
    suffix: str,
) -> None:
    repository = tmp_path / "full"
    repository.mkdir(parents=True)
    names = tuple(
        f"{stem}{suffix}"
        for stem in (
            "libmltavformat",
            "libmltqt6",
            "libmltglaxnimate-qt6",
            "libmltopencv",
        )
    )
    for name in names:
        (repository / name).write_bytes(name.encode("ascii"))
    contract = SimpleNamespace(
        target=SimpleNamespace(operating_system=operating_system),
        layout=SimpleNamespace(
            mlt_repository="full",
            mlt_preview_repository="preview",
        ),
    )

    prepare_runtime._prepare_preview_repository(tmp_path, contract)

    preview_repository = tmp_path / "preview"
    assert (preview_repository / names[0]).read_bytes() == names[0].encode("ascii")
    assert not (preview_repository / names[1]).exists()
    assert not (preview_repository / names[2]).exists()
    assert not (preview_repository / names[3]).exists()


def test_macos_runtime_repairs_framework_and_mlt_plugin_rpaths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "Shotcut.app"
    framework = bundle / "Contents" / "Frameworks" / "libavcodec.62.dylib"
    plugin = bundle / "Contents" / "PlugIns" / "mlt" / "libmltavformat.so"
    framework.parent.mkdir(parents=True)
    plugin.parent.mkdir(parents=True)
    framework.write_bytes(b"framework")
    plugin.write_bytes(b"plugin")
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        prepare_runtime.PlatformTarget,
        "current",
        lambda: PlatformTarget("macos", "arm64"),
    )

    def record(command: list[str], **_kwargs):
        commands.append(tuple(command))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(prepare_runtime.subprocess, "run", record)

    prepare_runtime._prepare_macos_runtime_rpaths(bundle)

    assert (
        "install_name_tool",
        "-add_rpath",
        "@loader_path",
        str(framework.resolve()),
    ) in commands
    assert (
        "install_name_tool",
        "-add_rpath",
        "@loader_path/../../Frameworks",
        str(plugin.resolve()),
    ) in commands
    signed = [command[-1] for command in commands if command[0] == "codesign"]
    assert signed == [str(framework.resolve()), str(plugin.resolve())]


def test_quality_workflow_provisions_and_exercises_every_media_runtime() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required_steps = (
        "scripts/prepare_runtime.py",
        "scripts/prepare_ci_qt.py",
        "scripts/build_native.py",
        "scripts/verify_development_runtime.py",
        "scripts/verify_display_capabilities.py",
        "test_drag_import_placement_snap_tracks_and_first_video_profile",
        "tests/v2/desktop/test_web_editor.py",
        "test_real_draw_element_failure_requires_clean_screenshot_retry",
        "tests/v2/integration/test_native_preview.py",
        "scripts.verify_web_render_performance",
        "scripts.verify_reference_comparison_chain",
        "scripts.verify_project_interchange",
    )
    assert all(step in workflow for step in required_steps)
    assert all(
        variable in workflow
        for variable in (
            "MEDIAFLOW_DEV_ROOT",
            "MEDIAFLOW_RUNTIME_DIR",
            "MEDIAFLOW_PROJECT_ROOT",
            "MEDIAFLOW_MEDIA_ROOT",
            "MEDIAFLOW_TEST_ROOT",
            "MEDIAFLOW_TEST_FIXTURE_ROOT",
            "MEDIAFLOW_MINIMUM_FREE_BYTES",
            "PYTHONUTF8",
        )
    )
    assert "--python-only" not in workflow
    assert "MEDIAFLOW_NATIVE_QML" not in workflow
    assert "PLAYWRIGHT_BROWSERS_PATH" not in workflow
    assert "apt-get install -y ffmpeg" not in workflow
    assert "brew install ffmpeg" not in workflow
    assert "playwright install chromium" not in workflow
    assert "vars.MEDIAFLOW_RUN_ONLINE_E2E == 'true'" in workflow
    assert not re.search(
        r"^\s*uses:\s*[^#\s]+@v\d+\s*(?:#.*)?$",
        workflow,
        flags=re.MULTILINE,
    )


def test_web_desktop_scenarios_run_in_separate_qtwebengine_processes() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    first_scenario = (
        "tests/v2/desktop/test_web_editor.py::"
        "test_unified_import_opens_the_v6_package_through_local_preview_server"
    )
    second_scenario = (
        "tests/v2/desktop/test_web_editor.py::"
        "test_real_dom_drag_crosses_webchannel_persists_and_is_read_back_by_page"
    )

    assert workflow.count("python -m pytest tests/v2/desktop/test_web_editor.py::") == 2
    assert first_scenario in workflow
    assert second_scenario in workflow
    assert "python -m pytest tests/v2/desktop/test_web_editor.py\n" not in workflow


def test_portable_ci_builds_and_executes_linux_and_apple_silicon_chains() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "portable-core:" in workflow
    assert "ubuntu-24.04" in workflow
    assert "macos-14" in workflow
    assert 'runner.arch }}-${{ hashFiles' in workflow
    assert "python scripts/prepare_runtime.py" in workflow
    assert "python scripts/prepare_ci_qt.py" in workflow
    assert "python scripts/build_native.py" in workflow
    assert "python scripts/verify_development_runtime.py --profile core" in workflow
    assert "tests/v2/infrastructure/test_web_media.py::test_v6_cli_chain" in workflow
    assert "tests/v2/integration/test_native_preview.py" in workflow
    assert "Type-check on the actual target platform" in workflow
    assert "linux-x86_64" in workflow
    assert "macos-arm64" in workflow
    required_xcb_packages = {
        "libx11-xcb-dev",
        "libxcb-cursor-dev",
        "libxcb-glx0-dev",
        "libxcb-icccm4-dev",
        "libxcb-image0-dev",
        "libxcb-keysyms1-dev",
        "libxcb-randr0-dev",
        "libxcb-render-util0-dev",
        "libxcb-shape0-dev",
        "libxcb-shm0-dev",
        "libxcb-sync-dev",
        "libxcb-util-dev",
        "libxcb-xfixes0-dev",
        "libxcb-xkb-dev",
        "libxcb1-dev",
        "libxkbcommon-x11-dev",
    }
    assert all(package in workflow for package in required_xcb_packages)
    assert "xvfb-run -a env QT_QPA_PLATFORM=xcb" in workflow
    assert "weston --backend=headless-backend.so" in workflow
    assert "QT_QPA_PLATFORM=wayland" in workflow


def test_ci_executes_all_six_cross_platform_project_routes() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "interchange-produce:" in workflow
    assert "interchange-consume:" in workflow
    routes = {
        ("windows-x86_64", "linux-x86_64"),
        ("windows-x86_64", "macos-arm64"),
        ("linux-x86_64", "windows-x86_64"),
        ("linux-x86_64", "macos-arm64"),
        ("macos-arm64", "windows-x86_64"),
        ("macos-arm64", "linux-x86_64"),
    }
    for producer, consumer in routes:
        route = re.compile(
            rf"producer:\s*{re.escape(producer)}\s+consumer:\s*{re.escape(consumer)}"
        )
        assert route.search(workflow), f"missing interchange route {producer} -> {consumer}"
    assert "--bundle \"${{ runner.temp }}/interchange-bundle\"" in workflow
    assert "interchange-${{ matrix.target }}" in workflow
    assert "interchange-${{ matrix.producer }}" in workflow


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
        "icu": ("libicui18n.so.73",),
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
                "schema_version": 2,
                "targets": {
                    "linux-x86_64": {
                        "qt": {
                            "version": "6.11.1",
                            "architecture": "linux_gcc_64",
                            "install_directory": "6.11.1/gcc_64",
                        },
                        "qt_archives": archives,
                    },
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

    installed = prepare_ci_qt.prepare_qt(
        qt_root,
        contract,
        target=PlatformTarget("linux", "x86_64"),
    )

    assert installed == (qt_root / "6.11.1/gcc_64").resolve()
    assert all(
        (installed / relative).is_file()
        for relative in prepare_ci_qt.REQUIRED_QT_FILES
    )
    assert (installed / "lib" / "libicui18n.so.73").is_file()
    assert not list(qt_root.glob("i-*"))
