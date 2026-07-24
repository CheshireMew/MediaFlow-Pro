from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
import sys
import wave
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import AsrSettings

from .asr_engine import FasterWhisperCliEngine
from .runtime_paths import RuntimePaths
from .subprocess_runner import run_cancellable, run_cancellable_streaming

ToolProgress = Callable[[OperationProgress], None]

FASTER_WHISPER_CLI_VERSION = "r245.4"
FASTER_WHISPER_CLI_ARCHIVE = "Faster-Whisper-XXL_r245.4_windows.7z"
FASTER_WHISPER_CLI_URL = (
    "https://github.com/Purfview/whisper-standalone-win/releases/download/"
    f"Faster-Whisper-XXL/{FASTER_WHISPER_CLI_ARCHIVE}"
)
FASTER_WHISPER_CLI_SIZE = 1_424_256_246
PYPI_YTDLP_URL = "https://pypi.org/pypi/yt-dlp/json"


def prepare_ytdlp_import(paths: RuntimePaths | None = None) -> Path | None:
    runtime = paths or RuntimePaths.discover()
    pointer = runtime.runtime_dir / "tools" / "yt-dlp-active.json"
    if not pointer.is_file():
        return None
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        package_root = Path(str(payload["path"])).resolve(strict=True)
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return None
    value = str(package_root)
    if value not in sys.path:
        sys.path.insert(0, value)
    return package_root


class RuntimeToolService:
    def __init__(
        self,
        settings: AsrSettings,
        paths: RuntimePaths | None = None,
        *,
        ytdlp_metadata_url: str = PYPI_YTDLP_URL,
        cli_url: str = FASTER_WHISPER_CLI_URL,
        cli_archive: str = FASTER_WHISPER_CLI_ARCHIVE,
        cli_size: int = FASTER_WHISPER_CLI_SIZE,
    ):
        self.settings = settings
        self.paths = paths or RuntimePaths.discover()
        self.ytdlp_metadata_url = ytdlp_metadata_url
        self.cli_url = cli_url
        self.cli_archive = cli_archive
        self.cli_size = cli_size

    def status(self, *, inspect_cuda: bool = False) -> dict:
        cli_path = self.resolve_cli_path()
        result = {
            "cliInstalled": cli_path is not None,
            "cliPath": str(cli_path) if cli_path else "",
            "cliVersion": FASTER_WHISPER_CLI_VERSION if cli_path else "",
            "ytDlpVersion": self.ytdlp_version() or "",
            "cudaStatus": "unchecked",
            "cudaSummary": "尚未检测 CUDA",
            "gpuName": "",
            "driverVersion": "",
        }
        if inspect_cuda:
            result.update(self.cuda_readiness())
        return result

    def resolve_cli_path(self) -> Path | None:
        candidates = [
            Path(self.settings.cli_path).expanduser() if self.settings.cli_path else None,
            self.paths.runtime_dir / "tools" / "Faster-Whisper-XXL" / "faster-whisper-xxl.exe",
        ]
        return next(
            (candidate.resolve() for candidate in candidates if candidate and candidate.is_file()),
            None,
        )

    def cuda_readiness(self) -> dict:
        gpu_name = ""
        driver_version = ""
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            try:
                result = subprocess.run(
                    [
                        nvidia_smi,
                        "--query-gpu=name,driver_version",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    gpu_name, _, driver_version = result.stdout.strip().partition(",")
                    gpu_name = gpu_name.strip()
                    driver_version = driver_version.strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            import ctranslate2

            device_count = int(ctranslate2.get_cuda_device_count())
        except (ImportError, OSError, RuntimeError):
            device_count = 0
        ready = bool(gpu_name and device_count > 0)
        return {
            "cudaStatus": "ready" if ready else "not_ready",
            "cudaSummary": (
                f"CUDA 可用，检测到 {device_count} 个设备"
                if ready
                else "内置 faster-whisper 当前不能使用 CUDA，可继续使用 CPU"
            ),
            "gpuName": gpu_name,
            "driverVersion": driver_version,
        }

    def ytdlp_version(self) -> str | None:
        pointer = self.paths.runtime_dir / "tools" / "yt-dlp-active.json"
        if pointer.is_file():
            try:
                value = str(json.loads(pointer.read_text(encoding="utf-8"))["version"])
                if value:
                    return value
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                pass
        try:
            prepare_ytdlp_import(self.paths)
            yt_dlp = importlib.import_module("yt_dlp")
            return str(yt_dlp.version.__version__)
        except (AttributeError, ImportError):
            return None

    def update_ytdlp(
        self,
        *,
        progress: ToolProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict:
        if progress:
            progress(OperationProgress.indeterminate("ytdlp_update_checking"))
        with urlopen(
            Request(self.ytdlp_metadata_url, headers={"User-Agent": "MediaFlow Pro setup"}),
            timeout=60,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        version = str(payload.get("info", {}).get("version") or "")
        wheel = next(
            (
                item
                for item in payload.get("urls", [])
                if str(item.get("filename") or "").endswith(".whl")
                and item.get("packagetype") == "bdist_wheel"
            ),
            None,
        )
        if not version or wheel is None:
            raise RuntimeError("PyPI 没有返回可安装的 yt-dlp wheel")
        downloads = self.paths.runtime_dir / "downloads"
        wheel_path = downloads / str(wheel["filename"])
        self._download_with_resume(
            str(wheel["url"]),
            wheel_path,
            int(wheel.get("size") or 0),
            progress=progress,
            check_cancelled=check_cancelled,
        )
        if progress:
            progress(OperationProgress.indeterminate("ytdlp_update_installing"))
        target = self.paths.runtime_dir / "tools" / "python" / f"yt-dlp-{version}"
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(wheel_path) as archive:
            for member in archive.infolist():
                if member.filename.startswith("yt_dlp/") or (
                    member.filename.startswith("yt_dlp-") and ".dist-info/" in member.filename
                ):
                    archive.extract(member, target)
        pointer = self.paths.runtime_dir / "tools" / "yt-dlp-active.json"
        self._write_json(pointer, {"version": version, "path": str(target.resolve())})
        return {"version": version, "path": str(target.resolve())}

    def install_faster_whisper_cli(
        self,
        *,
        progress: ToolProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Path:
        existing = self.resolve_cli_path()
        if existing:
            return existing
        tools_root = self.paths.runtime_dir / "tools"
        downloads = self.paths.runtime_dir / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(downloads)
        if usage.free < 4 * 1024**3:
            raise RuntimeError("安装 Faster-Whisper XXL 至少需要 4 GiB 可用空间")
        archive_path = downloads / self.cli_archive
        self._download_with_resume(
            self.cli_url,
            archive_path,
            self.cli_size,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        if progress:
            progress(OperationProgress.indeterminate("asr_cli_extracting"))
        tools_root.mkdir(parents=True, exist_ok=True)
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(tools_root)
        else:
            result = run_cancellable(
                ["tar", "-xf", str(archive_path), "-C", str(tools_root)],
                check_cancelled=check_cancelled,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "CLI 解压失败").strip())
        cli_path = tools_root / "Faster-Whisper-XXL" / "faster-whisper-xxl.exe"
        if not cli_path.is_file():
            raise RuntimeError(f"解压后没有找到 Faster-Whisper CLI：{cli_path}")
        return cli_path.resolve()

    def prewarm_cli(
        self,
        *,
        progress: ToolProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Path:
        cli_path = self.resolve_cli_path()
        if cli_path is None:
            raise FileNotFoundError("请先安装或选择 Faster-Whisper XXL")
        audio = self.paths.runtime_dir / "cache" / "asr-cli" / "prewarm.wav"
        if not audio.is_file():
            audio.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(b"\0\0" * 16_000)
        output_dir = (
            self.paths.runtime_dir
            / "cache"
            / "asr-cli"
            / "prewarm"
            / f"{self.settings.model}-{self.settings.device}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        settings = self.settings.model_copy(update={"cli_path": str(cli_path)})
        command = FasterWhisperCliEngine(settings, self.paths).build_command(audio, output_dir)
        if progress:
            progress(OperationProgress.indeterminate("asr_cli_prewarming"))

        def observe(line: str) -> None:
            match = re.search(r"(?<![\d.])(\d{1,3})%", line)
            if match and progress:
                progress(
                    OperationProgress.determinate(
                        "asr_cli_prewarming",
                        completed=min(100, int(match.group(1))),
                        total=100,
                        unit="percent",
                    )
                )

        result = run_cancellable_streaming(
            command,
            on_stdout_line=observe,
            on_stderr_line=observe,
            check_cancelled=check_cancelled,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stdout or result.stderr or "CLI 预热失败").strip())
        return cli_path

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _download_with_resume(
        url: str,
        destination: Path,
        expected_size: int,
        *,
        progress: ToolProgress | None,
        check_cancelled: Callable[[], None] | None,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, 5):
            current_size = destination.stat().st_size if destination.is_file() else 0
            if expected_size and current_size == expected_size:
                return
            headers = {"User-Agent": "MediaFlow Pro setup"}
            if current_size and (not expected_size or current_size < expected_size):
                headers["Range"] = f"bytes={current_size}-"
            elif expected_size and current_size > expected_size:
                current_size = 0
            try:
                with urlopen(Request(url, headers=headers), timeout=60) as response:
                    status = getattr(response, "status", 200)
                    append = current_size > 0 and status == 206
                    if not append:
                        current_size = 0
                    total = expected_size or int(response.headers.get("Content-Length") or 0)
                    with destination.open("ab" if append else "wb") as output:
                        while chunk := response.read(1024 * 1024):
                            if check_cancelled:
                                check_cancelled()
                            output.write(chunk)
                            current_size += len(chunk)
                            if progress and total:
                                progress(
                                    OperationProgress.determinate(
                                        "runtime_tool_downloading",
                                        completed=min(current_size, total),
                                        total=total,
                                        unit="bytes",
                                    )
                                )
                break
            except (TimeoutError, URLError, OSError) as error:
                if attempt == 4:
                    raise RuntimeError(f"运行时工具下载失败：{error}") from error
        final_size = destination.stat().st_size if destination.is_file() else 0
        if expected_size and final_size != expected_size:
            raise RuntimeError(f"运行时工具下载不完整：{final_size} / {expected_size}")
