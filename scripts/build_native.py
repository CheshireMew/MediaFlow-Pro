from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from mediaflow.infrastructure.runtime_contract import PlatformTarget, load_runtime_contract
from mediaflow.infrastructure.runtime_paths import runtime_directory

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mediaflow" / "desktop" / "native"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the MediaFlow native QML plugin")
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--qt-dir", type=Path)
    parser.add_argument("--runtime-root", type=Path, default=runtime_directory())
    return parser


def _command(name: str) -> str:
    environment_command = Path(sys.executable).parent / (
        f"{name}.exe" if PlatformTarget.current().operating_system == "windows" else name
    )
    if environment_command.is_file():
        return str(environment_command)
    executable = shutil.which(name)
    if executable is None:
        raise FileNotFoundError(f"Required native build command was not found: {name}")
    return executable


def _windows_vcvars() -> Path:
    program_files_value = os.environ.get("ProgramFiles(x86)")
    if not program_files_value:
        raise RuntimeError("Windows did not publish the ProgramFiles(x86) tool root")
    program_files = Path(program_files_value)
    vswhere = program_files / "Microsoft Visual Studio/Installer/vswhere.exe"
    if not vswhere.is_file():
        raise FileNotFoundError("Visual Studio vswhere.exe was not found")
    completed = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    root = Path(completed.stdout.strip())
    vcvars = root / "VC/Auxiliary/Build/vcvars64.bat"
    if not vcvars.is_file():
        raise FileNotFoundError(vcvars)
    return vcvars


def _run_build_command(target: PlatformTarget, arguments: list[str]) -> None:
    if target.operating_system != "windows":
        subprocess.run(arguments, check=True)
        return
    quoted = subprocess.list2cmdline(arguments)
    command = f'cmd.exe /d /c call "{_windows_vcvars()}" && {quoted}'
    subprocess.run(command, check=True)


def build_native(
    *,
    build_dir: Path | None,
    qt_dir: Path | None,
    runtime_root: Path,
) -> Path:
    target = PlatformTarget.current()
    contract = load_runtime_contract(target=target)
    runtime = runtime_root.expanduser().resolve()
    selected_build = (
        build_dir or runtime / "native" / target.key
    ).expanduser().resolve()
    selected_qt = (
        qt_dir or runtime / "qt" / contract.qt_install_directory
    ).expanduser().resolve()
    qt_config = selected_qt / "lib" / "cmake" / "Qt6" / "Qt6Config.cmake"
    if not qt_config.is_file():
        raise FileNotFoundError(f"Qt {contract.qt_version} SDK was not found: {qt_config}")
    selected_build.mkdir(parents=True, exist_ok=True)
    configure_arguments = [
        _command("cmake"),
        "-S",
        str(SOURCE),
        "-B",
        str(selected_build),
        "-G",
        "Ninja",
        f"-DCMAKE_MAKE_PROGRAM={_command('ninja')}",
        f"-DCMAKE_PREFIX_PATH={selected_qt}",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    if target.operating_system == "macos":
        configure_arguments.extend(
            [
                "-DCMAKE_OSX_ARCHITECTURES=arm64",
                "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0",
            ]
        )
    _run_build_command(target, configure_arguments)
    _run_build_command(
        target,
        [_command("cmake"), "--build", str(selected_build), "--config", "Release"],
    )
    plugin_directory = selected_build / "qml" / "MediaFlow" / "Native"
    candidates = tuple(plugin_directory.glob("*mediaflownativeplugin*"))
    if not candidates:
        raise RuntimeError(f"Native preview plugin was not produced: {plugin_directory}")
    return candidates[0]


def main() -> int:
    args = _parser().parse_args()
    print(
        build_native(
            build_dir=args.build_dir,
            qt_dir=args.qt_dir,
            runtime_root=args.runtime_root,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
