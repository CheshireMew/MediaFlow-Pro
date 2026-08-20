from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from mediaflow.domain.model_base import now_ms
from mediaflow.domain.runtime_capabilities import (
    RuntimeCapabilityStatus,
    RuntimeInspection,
)
from mediaflow.domain.settings import ServiceSettings
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.ffprobe_runner import FfprobeRunner
from mediaflow.infrastructure.runtime_components import RuntimeComponentService
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_contract import (
    PlatformTarget,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.runtime_tools import RuntimeToolService
from mediaflow.infrastructure.settings_repository import ServiceSettingsRepository
from mediaflow.infrastructure.speaker_diarization import (
    pyannote_model_ready_marker,
)
from mediaflow.infrastructure.subprocess_runner import run_cancellable


class RuntimeCapabilityInspector:
    def __init__(
        self,
        *,
        settings: ServiceSettings | None = None,
        runtime: RuntimeContext,
    ):
        self.settings = settings
        self.runtime = runtime

    def inspect(self) -> RuntimeInspection:
        contract = self.runtime.contract
        paths = self.runtime.paths
        ffmpeg_version = contract.ffmpeg_version
        melt_version = contract.melt_version
        ffmpeg = FfmpegRunner(paths.ffmpeg)
        ffprobe = FfprobeRunner(paths.ffprobe)
        capabilities = [
            self._probe_media_command(
                "ffmpeg",
                ffmpeg,
                contract.ffmpeg_probe_arguments,
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
            self._probe_media_command(
                "ffprobe",
                ffprobe,
                contract.ffmpeg_probe_arguments,
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
                contract.melt_probe_arguments,
                expected=(
                    lambda _first, output: melt_version in output
                ),
                expected_description=f"MLT {melt_version}",
            ),
            self._probe_chromium(
                paths.chromium,
                contract.playwright.browser_version,
            ),
            self._probe_native_preview(
                paths.native_qml,
                paths.target,
                contract.qt_version,
            ),
        ]
        settings = self.settings or ServiceSettingsRepository().load()
        try:
            component_status = RuntimeComponentService(
                settings,
                paths,
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
        capabilities.append(self._probe_speaker_diarization(settings, paths))
        return RuntimeInspection(
            checked_at=now_ms(),
            runtime_root=str(paths.runtime_dir),
            capabilities=capabilities,
        )

    @staticmethod
    def _probe_speaker_diarization(
        settings: ServiceSettings,
        paths: RuntimePaths,
    ) -> RuntimeCapabilityStatus:
        configured = settings.speaker_diarization
        if configured.backend == "transcript_clustering":
            status = RuntimeToolService(settings, paths).speaker_clustering_status()
            if not status["ready"]:
                return RuntimeCapabilityStatus(
                    id="speaker-diarization",
                    status="unavailable",
                    path=str(status["python"]),
                    reason=str(status["reason"]),
                )
            return RuntimeCapabilityStatus(
                id="speaker-diarization",
                status="ready",
                version=str(status["version"]),
                path=str(status["model"]),
            )
        if not configured.python_executable:
            return RuntimeCapabilityStatus(
                id="speaker-diarization",
                status="unavailable",
                reason="尚未选择安装了 pyannote.audio 的独立 Python 环境",
            )
        executable = Path(configured.python_executable).expanduser()
        if not executable.is_file():
            return RuntimeCapabilityStatus(
                id="speaker-diarization",
                status="unavailable",
                path=str(executable),
                reason="说话人识别 Python 不存在",
            )
        model_path = Path(configured.model).expanduser()
        token = configured.hugging_face_token.strip() or os.environ.get("HF_TOKEN", "").strip()
        if (
            not model_path.exists()
            and not token
            and not pyannote_model_ready_marker(
                executable,
                configured.model,
            ).is_file()
        ):
            return RuntimeCapabilityStatus(
                id="speaker-diarization",
                status="unavailable",
                path=str(executable.resolve()),
                reason="Community-1 尚未完成首次授权识别，且没有配置 Hugging Face 访问令牌",
            )
        try:
            completed = run_cancellable(
                [
                    str(executable),
                    "-c",
                    (
                        "import pyannote.audio, torch; "
                        "print(getattr(pyannote.audio, '__version__', 'unknown'))"
                    ),
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return RuntimeCapabilityStatus(
                id="speaker-diarization",
                status="unavailable",
                path=str(executable.resolve()),
                reason=f"pyannote.audio 探测失败：{error}",
            )
        output = "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        ).strip()
        version_line = next(
            (line.strip() for line in output.splitlines() if line.strip()),
            "",
        )
        if completed.returncode != 0 or not version_line:
            return RuntimeCapabilityStatus(
                id="speaker-diarization",
                status="unavailable",
                path=str(executable.resolve()),
                reason=output[-2000:] or f"探测退出码 {completed.returncode}",
            )
        return RuntimeCapabilityStatus(
            id="speaker-diarization",
            status="ready",
            version=version_line,
            path=str(executable.resolve()),
        )

    @staticmethod
    def _probe_media_command(
        capability_id: str,
        runner: FfmpegRunner | FfprobeRunner,
        arguments: tuple[str, ...],
        *,
        expected: Callable[[str, str], bool],
        expected_description: str,
    ) -> RuntimeCapabilityStatus:
        try:
            completed = runner.run(arguments, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            return RuntimeCapabilityStatus(
                id=capability_id,
                status="unavailable",
                path=str(runner.executable),
                reason=f"Probe failed: {error}",
            )
        return RuntimeCapabilityInspector._probe_result(
            capability_id,
            runner.executable,
            completed,
            expected=expected,
            expected_description=expected_description,
        )

    @staticmethod
    def _probe_command(
        capability_id: str,
        executable: Path | None,
        arguments: tuple[str, ...],
        *,
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
        return RuntimeCapabilityInspector._probe_result(
            capability_id,
            executable,
            completed,
            expected=expected,
            expected_description=expected_description,
        )

    @staticmethod
    def _probe_result(
        capability_id: str,
        executable: Path,
        completed: subprocess.CompletedProcess,
        *,
        expected: Callable[[str, str], bool],
        expected_description: str,
    ) -> RuntimeCapabilityStatus:
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
    def _probe_chromium(
        executable: Path | None,
        expected_version: str,
    ) -> RuntimeCapabilityStatus:
        if executable is None:
            return RuntimeCapabilityStatus(
                id="chromium",
                status="unavailable",
                reason="Pinned Playwright Chromium was not found",
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
        if browser_version != expected_version:
            return RuntimeCapabilityStatus(
                id="chromium",
                status="unavailable",
                version=browser_version,
                path=str(executable),
                reason=f"Expected Chromium {expected_version}",
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
        target: PlatformTarget,
        expected_qt_version: str,
    ) -> RuntimeCapabilityStatus:
        if native_qml is None:
            return RuntimeCapabilityStatus(
                id="native-preview",
                status="unavailable",
                reason="Native preview QML root was not found",
            )
        plugin_name = {
            "windows": "mediaflownativeplugin.dll",
            "linux": "libmediaflownativeplugin.so",
            "macos": "libmediaflownativeplugin.dylib",
        }[target.operating_system]
        plugin = native_qml / "MediaFlow" / "Native" / plugin_name
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


class RuntimeInspectionService:
    def __init__(
        self,
        runtime: RuntimeContext,
        settings: Callable[[], ServiceSettings],
    ) -> None:
        self._runtime = runtime
        self._settings = settings

    def inspect(self) -> RuntimeInspection:
        return RuntimeCapabilityInspector(
            settings=self._settings(),
            runtime=self._runtime,
        ).inspect()
