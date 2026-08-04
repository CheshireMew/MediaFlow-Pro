from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _launch_windows_service() -> int:
    """Ask the current-user WMI provider to own the resident process."""

    import pythoncom  # type: ignore[import-untyped]
    import win32com.client  # type: ignore[import-untyped]

    environment = os.environ.copy()
    environment["PYTHONFAULTHANDLER"] = environment.pop(
        "_MEDIAFLOW_SERVICE_PYTHONFAULTHANDLER",
        "1",
    )
    command = [sys.executable, "-m", "mediaflow.service"]
    pythoncom.CoInitialize()
    try:
        locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        services = locator.ConnectServer(".", r"root\cimv2")
        process_class = services.Get("Win32_Process")
        inputs = process_class.Methods_("Create").InParameters.SpawnInstance_()
        inputs.Properties_.Item("CommandLine").Value = subprocess.list2cmdline(command)
        inputs.Properties_.Item("CurrentDirectory").Value = str(Path.cwd())

        startup = services.Get("Win32_ProcessStartup").SpawnInstance_()
        startup.Properties_.Item("CreateFlags").Value = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
        startup.Properties_.Item("ShowWindow").Value = 0
        startup.Properties_.Item("EnvironmentVariables").Value = [
            f"{name}={value}" for name, value in environment.items()
        ]
        inputs.Properties_.Item("ProcessStartupInformation").Value = startup

        output = services.ExecMethod("Win32_Process", "Create", inputs)
        return_value = int(output.Properties_.Item("ReturnValue").Value)
        if return_value != 0:
            raise OSError(
                f"WMI could not start MediaFlow Editor Service (code {return_value})"
            )
        return int(output.Properties_.Item("ProcessId").Value)
    finally:
        pythoncom.CoUninitialize()


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("The WMI service launcher is available only on Windows")
    print(_launch_windows_service())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
