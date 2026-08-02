from __future__ import annotations

import subprocess
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from mediaflow.domain.model_base import now_ms
from mediaflow.domain.runtime_capabilities import (
    RuntimeCapabilityStatus,
    RuntimeInspection,
)
from mediaflow.domain.settings import GlobalSettings
from mediaflow.infrastructure.chromium_runtime import (
    discover_chromium_executable,
)
from mediaflow.infrastructure.runtime_components import RuntimeComponentService
from mediaflow.infrastructure.runtime_contract import (
    DEFAULT_RUNTIME_CONTRACT,
    RuntimeContract,
    load_runtime_contract,
)
from mediaflow.infrastructure.runtime_paths import RuntimePathDiscovery
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.subprocess_runner import run_cancellable


class RuntimeCapabilityInspector:
    def __init__(
        self,
        contract_path: str | Path = DEFAULT_RUNTIME_CONTRACT,
        *,
        settings: GlobalSettings | None = None,
    ):
        self.contract_path = Path(contract_path).resolve()
        self.settings = settings

    def inspect(self) -> RuntimeInspection:
        contract, contract_error = self._load_contract()
        try:
            paths = RuntimePathDiscovery.discover()
        except Exception as error:
            reason = f"Runtime paths could not be discovered: {error}"
            return RuntimeInspection(
                checked_at=now_ms(),
                runtime_root="",
                capabilities=[
                    RuntimeCapabilityStatus(
                        id=capability_id,
                        status="unavailable",
                        reason=reason,
                    )
                    for capability_id in (
                        "ffmpeg",
                        "ffprobe",
                        "mlt",
                        "chromium",
                        "native-preview",
                        "faster-whisper-xxl",
                        "gpt-sovits-v2pro",
                    )
                ],
            )

        ffmpeg_version = contract.ffmpeg_version if contract else ""
        melt_version = contract.melt_version if contract else ""
        qt_version = contract.qt_version if contract else ""
        capabilities = [
            self._probe_command(
                "ffmpeg",
                paths.ffmpeg,
                ("-version",),
                contract_error=contract_error,
                expected=(
                    lambda first, output: (
                        first.startswith(f"ffmpeg version {ffmpeg_version} ")
                        and "--enable-gpl" in output
                        and "--enable-version3" in output
                    )
                ),
                expected_description=(
                    f"FFmpeg {ffmpeg_version} with the reviewed GPLv3 configuration"
                ),
            ),
            self._probe_command(
                "ffprobe",
                paths.ffprobe,
                ("-version",),
                contract_error=contract_error,
                expected=(
                    lambda first, _output: first.startswith(
                        f"ffprobe version {ffmpeg_version} "
                    )
                ),
                expected_description=f"FFprobe {ffmpeg_version}",
            ),
            self._probe_command(
                "mlt",
                paths.melt,
                ("-version",),
                contract_error=contract_error,
                expected=(
                    lambda first, _output: first == f"melt.exe {melt_version}"
                ),
                expected_description=f"MLT {melt_version}",
            ),
            self._probe_chromium(),
            self._probe_native_preview(
                paths.native_qml,
                qt_version,
                contract_error,
            ),
        ]
        try:
            settings = self.settings or SettingsRepository().load()
            component_status = RuntimeComponentService(
                settings,
                _runtime_paths(paths),
            ).status(probe=True)
            capabilities.extend(
                RuntimeCapabilityStatus(
                    id=component_id,
                    status="ready" if status["ready"] else "unavailable",
                    version=str(status["version"]),
                    path=str(status["entrypoint"] or status["path"]),
                    reason=str(status["reason"]),
                )
                for component_id, status in component_status.items()
            )
        except Exception as error:
            capabilities.extend(
                RuntimeCapabilityStatus(
                    id=component_id,
                    status="unavailable",
                    reason=f"Runtime component inspection failed: {error}",
                )
                for component_id in ("faster-whisper-xxl", "gpt-sovits-v2pro")
            )
        return RuntimeInspection(
            checked_at=now_ms(),
            runtime_root=str(paths.runtime_dir),
            capabilities=capabilities,
        )

    def _load_contract(self) -> tuple[RuntimeContract | None, str]:
        try:
            return load_runtime_contract(self.contract_path), ""
        except (OSError, ValueError) as error:
            return None, f"Runtime contract is unavailable: {error}"

    @staticmethod
    def _probe_command(
        capability_id: str,
        executable: Path | None,
        arguments: tuple[str, ...],
        *,
        contract_error: str,
        expected: Callable[[str, str], bool],
        expected_description: str,
    ) -> RuntimeCapabilityStatus:
        if executable is None:
            return RuntimeCapabilityStatus(
                id=capability_id,
                status="unavailable",
                reason=f"{capability_id} executable was not found",
            )
        try:
            completed = run_cancellable(
                [str(executable), *arguments],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return RuntimeCapabilityStatus(
                id=capability_id,
                status="unavailable",
                path=str(executable),
                reason=f"Probe failed: {error}",
            )
        output = "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        )
        first_line = next(
            (line.strip() for line in output.splitlines() if line.strip()),
            "",
        )
        if completed.returncode != 0 or not first_line:
            return RuntimeCapabilityStatus(
                id=capability_id,
                status="unavailable",
                path=str(executable),
                reason=(
                    first_line
                    or f"Probe exited with code {completed.returncode}"
                ),
            )
        if contract_error:
            return RuntimeCapabilityStatus(
                id=capability_id,
                status="unverified",
                version=first_line,
                path=str(executable),
                reason=contract_error,
            )
        if not expected(first_line, output):
            return RuntimeCapabilityStatus(
                id=capability_id,
                status="unavailable",
                version=first_line,
                path=str(executable),
                reason=f"Expected {expected_description}",
            )
        return RuntimeCapabilityStatus(
            id=capability_id,
            status="ready",
            version=first_line,
            path=str(executable),
        )

    @staticmethod
    def _probe_chromium() -> RuntimeCapabilityStatus:
        executable = discover_chromium_executable()
        if executable is None:
            return RuntimeCapabilityStatus(
                id="chromium",
                status="unavailable",
                reason="Chrome or Edge was not found",
            )
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=str(executable),
                    headless=True,
                )
                try:
                    page = browser.new_page()
                    page.set_content("<main id='runtime-proof'>ready</main>")
                    if page.text_content("#runtime-proof") != "ready":
                        raise RuntimeError(
                            "Chromium did not execute the runtime smoke page"
                        )
                    browser_version = browser.version
                finally:
                    browser.close()
        except Exception as error:
            return RuntimeCapabilityStatus(
                id="chromium",
                status="unavailable",
                path=str(executable),
                reason=f"Browser smoke test failed: {error}",
            )
        return RuntimeCapabilityStatus(
            id="chromium",
            status="ready",
            version=browser_version,
            path=str(executable),
        )

    @staticmethod
    def _probe_native_preview(
        native_qml: Path | None,
        expected_qt_version: str,
        contract_error: str,
    ) -> RuntimeCapabilityStatus:
        if native_qml is None:
            return RuntimeCapabilityStatus(
                id="native-preview",
                status="unavailable",
                reason="Native preview QML root was not found",
            )
        plugin = (
            native_qml
            / "MediaFlow"
            / "Native"
            / "mediaflownativeplugin.dll"
        )
        qmldir = native_qml / "MediaFlow" / "Native" / "qmldir"
        if not plugin.is_file() or not qmldir.is_file():
            return RuntimeCapabilityStatus(
                id="native-preview",
                status="unavailable",
                path=str(native_qml),
                reason="Native preview QML package is incomplete",
            )
        try:
            pyside_version = version("PySide6")
        except PackageNotFoundError:
            return RuntimeCapabilityStatus(
                id="native-preview",
                status="unavailable",
                path=str(plugin),
                reason="PySide6 is not installed",
            )
        if contract_error:
            return RuntimeCapabilityStatus(
                id="native-preview",
                status="unverified",
                version=pyside_version,
                path=str(plugin),
                reason=contract_error,
            )
        if pyside_version != expected_qt_version:
            return RuntimeCapabilityStatus(
                id="native-preview",
                status="unavailable",
                version=pyside_version,
                path=str(plugin),
                reason=f"Expected PySide6 {expected_qt_version}",
            )
        return RuntimeCapabilityStatus(
            id="native-preview",
            status="ready",
            version=pyside_version,
            path=str(plugin),
        )


def _runtime_paths(discovery: RuntimePathDiscovery):
    from mediaflow.infrastructure.runtime_paths import RuntimePaths

    if discovery.ffmpeg is None or discovery.ffprobe is None:
        raise FileNotFoundError("FFmpeg runtime is incomplete")
    return RuntimePaths(
        runtime_dir=discovery.runtime_dir,
        ffmpeg=discovery.ffmpeg,
        ffprobe=discovery.ffprobe,
        melt=discovery.melt,
        native_qml=discovery.native_qml,
    )
