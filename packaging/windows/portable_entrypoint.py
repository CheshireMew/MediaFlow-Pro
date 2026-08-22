from __future__ import annotations

import ctypes
import errno
import os
import runpy
import sys
from pathlib import Path


def _portable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _configure_portable_environment() -> None:
    root = _portable_root()
    user_data = root / "UserData"
    settings = user_data / "Settings"
    defaults = {
        "MEDIAFLOW_RUNTIME_DIR": root / "runtime",
        "MEDIAFLOW_PROJECT_ROOT": user_data / "Projects",
        "MEDIAFLOW_MEDIA_ROOT": user_data / "Media",
        "MEDIAFLOW_SERVICE_STATE_DIR": user_data / "Service",
        "MEDIAFLOW_SERVICE_SETTINGS_PATH": settings / "service-settings.json",
        "MEDIAFLOW_DESKTOP_SETTINGS_PATH": settings / "desktop-settings.json",
    }
    for name, path in defaults.items():
        os.environ.setdefault(name, str(path.resolve()))


def _restore_redirected_standard_streams() -> None:
    """Restore usable standard streams for CLI modes of the windowed executable."""

    if os.name != "nt":
        return

    def stream_is_usable(stream_name: str) -> bool:
        stream = getattr(sys, stream_name, None)
        if stream is None:
            return False
        try:
            os.fstat(stream.fileno())
        except (AttributeError, OSError, ValueError):
            return False
        return True

    kernel32 = ctypes.windll.kernel32
    handles = {
        "stdin": (-10, os.O_RDONLY, "r"),
        "stdout": (-11, os.O_WRONLY, "w"),
        "stderr": (-12, os.O_WRONLY, "w"),
    }
    import msvcrt

    for stream_name, (handle_id, flags, mode) in handles.items():
        if stream_is_usable(stream_name):
            continue
        handle = kernel32.GetStdHandle(handle_id)
        if handle in (0, -1):
            continue
        try:
            descriptor = msvcrt.open_osfhandle(handle, flags)
            stream = os.fdopen(
                descriptor,
                mode,
                encoding="utf-8",
                errors="replace",
                buffering=1,
                closefd=False,
            )
        except OSError:
            continue
        setattr(sys, stream_name, stream)

    missing_console_streams = [
        stream_name
        for stream_name in ("stdin", "stdout", "stderr")
        if not stream_is_usable(stream_name)
    ]
    if not missing_console_streams:
        return

    # PowerShell/cmd may launch a windowed executable with placeholder standard
    # handles instead of inherited pipes. Attach to the parent console only when
    # those handles are unusable; subprocess pipes remain untouched.
    kernel32.AttachConsole(ctypes.c_uint(-1).value)
    kernel32.SetConsoleOutputCP(65001)
    console_targets = {
        "stdin": ("CONIN$", "r"),
        "stdout": ("CONOUT$", "w"),
        "stderr": ("CONOUT$", "w"),
    }
    for stream_name in missing_console_streams:
        target, mode = console_targets[stream_name]
        try:
            stream = open(
                target,
                mode,
                encoding="utf-8",
                errors="replace",
                buffering=1,
            )
        except OSError:
            continue
        setattr(sys, stream_name, stream)


class _FaultTolerantOutput:
    """Keep a windowed CLI invocation from crashing on placeholder pipes."""

    def __init__(self, stream: object) -> None:
        self._stream = stream

    def write(self, value: str) -> int:
        try:
            return self._stream.write(value)
        except OSError as error:
            if error.errno not in {errno.EBADF, errno.EINVAL, errno.EPIPE}:
                raise
            return len(value)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except OSError as error:
            if error.errno not in {errno.EBADF, errno.EINVAL, errno.EPIPE}:
                raise

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)


def _guard_standard_outputs() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and not isinstance(stream, _FaultTolerantOutput):
            setattr(sys, stream_name, _FaultTolerantOutput(stream))


def _isolate_editor_service_qt_environment() -> None:
    """Do not leak PyInstaller's PySide plugin paths into MLT subprocesses."""

    for name in (
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QML_IMPORT_PATH",
        "QML2_IMPORT_PATH",
    ):
        os.environ.pop(name, None)


def _run_module(module: str, arguments: list[str]) -> int:
    sys.argv = [module, *arguments]
    if module == "mediaflow.service.windows_launcher":
        from mediaflow.service.windows_launcher import _launch_windows_service

        _launch_windows_service()
        return 0
    if module == "mediaflow.service":
        _isolate_editor_service_qt_environment()
        from mediaflow.service.__main__ import main

        return main()
    if module == "mediaflow.cli":
        from mediaflow.cli import main

        return main()
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


def main() -> int:
    _configure_portable_environment()
    arguments = sys.argv[1:]
    if len(arguments) >= 2 and arguments[0] == "-m":
        _restore_redirected_standard_streams()
        _guard_standard_outputs()
        return _run_module(arguments[1], arguments[2:])
    from mediaflow.desktop.app import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    raise SystemExit(main())
