from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import wave
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.request import Request, urlopen

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.runtime_capabilities import RuntimeComponentInstallResult
from mediaflow.domain.settings import ServiceSettings
from mediaflow.file_digest import sha256_file

from .asr_engine import FasterWhisperCliEngine
from .resumable_download import DownloadSizeError, DownloadTransferError, download_with_resume
from .runtime_components import RuntimeComponentService
from .runtime_paths import RuntimePaths
from .subprocess_runner import run_cancellable_streaming

ToolProgress = Callable[[OperationProgress], None]

PYPI_YTDLP_URL = "https://pypi.org/pypi/yt-dlp/json"
SPEAKER_CLUSTERING_VERSION = "1.13.5"
SPEAKER_CLUSTERING_MODEL = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
SPEAKER_CLUSTERING_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"speaker-recongition-models/{SPEAKER_CLUSTERING_MODEL}"
)
SPEAKER_CLUSTERING_MODEL_SIZE = 28_281_164
SPEAKER_CLUSTERING_MODEL_SHA256 = "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"


def prepare_ytdlp_import(paths: RuntimePaths) -> Path | None:
    runtime = paths
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
        settings: ServiceSettings,
        paths: RuntimePaths,
        *,
        ytdlp_metadata_url: str = PYPI_YTDLP_URL,
        component_catalog_path: str | Path | None = None,
    ):
        self.settings = settings
        self.paths = paths
        self.ytdlp_metadata_url = ytdlp_metadata_url
        self.components = (
            RuntimeComponentService(settings, self.paths)
            if component_catalog_path is None
            else RuntimeComponentService(
                settings,
                self.paths,
                catalog_path=component_catalog_path,
            )
        )

    def status(self, *, inspect_cuda: bool = False) -> dict:
        result = {
            "components": self.components.status(),
            "ytDlpVersion": self.ytdlp_version() or "",
            "speakerClustering": self.speaker_clustering_status(),
            "cudaStatus": "unchecked",
            "cudaSummary": "尚未检测 CUDA",
            "gpuName": "",
            "driverVersion": "",
        }
        if inspect_cuda:
            result.update(self.cuda_readiness())
        return result

    def speaker_clustering_status(self) -> dict:
        configured = self.settings.speaker_diarization
        default_root = self.paths.runtime_dir / "tools" / f"speaker-clustering-{SPEAKER_CLUSTERING_VERSION}"
        default_python = self.paths.target.virtual_environment_python(default_root / "venv")
        python = Path(configured.clustering_python_executable or default_python).expanduser()
        model = Path(
            configured.embedding_model_path or default_root / "models" / SPEAKER_CLUSTERING_MODEL
        ).expanduser()
        if not python.is_file() or not model.is_file():
            return {
                "ready": False,
                "version": "",
                "python": str(python),
                "model": str(model),
                "reason": "尚未安装本地 3D-Speaker 音色模型",
            }
        if model.stat().st_size != SPEAKER_CLUSTERING_MODEL_SIZE:
            return {
                "ready": False,
                "version": "",
                "python": str(python.resolve()),
                "model": str(model.resolve()),
                "reason": "3D-Speaker 模型大小不正确，请重新安装",
            }
        try:
            completed = run_cancellable_streaming(
                [
                    str(python),
                    "-c",
                    (
                        "import importlib.metadata, numpy, sherpa_onnx; "
                        "print(importlib.metadata.version('sherpa-onnx'))"
                    ),
                ],
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "ready": False,
                "version": "",
                "python": str(python.resolve()),
                "model": str(model.resolve()),
                "reason": f"本地说话人识别探测失败：{error}",
            }
        version = (completed.stdout or "").strip().splitlines()
        if completed.returncode != 0 or not version:
            detail = (completed.stderr or completed.stdout or "运行环境不可用").strip()
            return {
                "ready": False,
                "version": "",
                "python": str(python.resolve()),
                "model": str(model.resolve()),
                "reason": detail[-2000:],
            }
        return {
            "ready": True,
            "version": version[-1],
            "python": str(python.resolve()),
            "model": str(model.resolve()),
            "reason": "",
        }

    def resolve_cli_path(self) -> Path | None:
        installation = self.components.resolve("faster-whisper-xxl")
        return installation.entrypoint if installation else None

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

        def report_download(completed: int, total: int) -> None:
            if progress:
                progress(
                    OperationProgress.determinate(
                        "runtime_tool_downloading",
                        completed=completed,
                        total=total,
                        unit="bytes",
                    )
                )

        try:
            download_with_resume(
                str(wheel["url"]),
                wheel_path,
                int(wheel.get("size") or 0),
                progress=report_download,
                check_cancelled=check_cancelled,
            )
        except DownloadSizeError as error:
            raise RuntimeError(f"运行时工具下载不完整：{error.actual} / {error.expected}") from error
        except DownloadTransferError as error:
            raise RuntimeError(f"运行时工具下载失败：{error}") from error
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

    def install_components(
        self,
        component_ids: list[str],
        *,
        progress: ToolProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, RuntimeComponentInstallResult]:
        return self.components.install_selected(
            component_ids,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    def install_speaker_clustering(
        self,
        *,
        progress: ToolProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, str]:
        root = self.paths.runtime_dir / "tools" / f"speaker-clustering-{SPEAKER_CLUSTERING_VERSION}"
        venv = root / "venv"
        python = self.paths.target.virtual_environment_python(venv)
        model = root / "models" / SPEAKER_CLUSTERING_MODEL
        pip_cache = self.paths.runtime_dir / "cache" / "pip"
        root.mkdir(parents=True, exist_ok=True)
        pip_cache.mkdir(parents=True, exist_ok=True)
        if not python.is_file():
            if progress:
                progress(OperationProgress.indeterminate("speaker_clustering_creating_environment"))
            result = run_cancellable_streaming(
                [sys.executable, "-m", "venv", str(venv)],
                check_cancelled=check_cancelled,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "无法创建本地说话人识别环境：" + (result.stderr or result.stdout or "未知错误")[-3000:]
                )
        if progress:
            progress(OperationProgress.indeterminate("speaker_clustering_installing_runtime"))
        install = run_cancellable_streaming(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                f"sherpa-onnx=={SPEAKER_CLUSTERING_VERSION}",
                "numpy==2.2.6",
            ],
            check_cancelled=check_cancelled,
            env={**os.environ, "PIP_CACHE_DIR": str(pip_cache)},
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        if install.returncode != 0:
            raise RuntimeError(
                "本地说话人识别运行库安装失败：" + (install.stderr or install.stdout or "未知错误")[-4000:]
            )
        check = run_cancellable_streaming(
            [str(python), "-m", "pip", "check"],
            check_cancelled=check_cancelled,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if check.returncode != 0:
            raise RuntimeError(
                "本地说话人识别依赖检查失败：" + (check.stderr or check.stdout or "未知错误")[-3000:]
            )
        if model.is_file() and sha256_file(model) != SPEAKER_CLUSTERING_MODEL_SHA256:
            archive = (
                self.paths.runtime_dir
                / "archive"
                / "speaker-clustering"
                / f"{model.name}.{sha256_file(model)[:16]}.invalid"
            )
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.exists():
                archive = archive.with_name(f"{archive.stem}-{model.stat().st_mtime_ns}{archive.suffix}")
            model.replace(archive)

        def report_download(completed: int, total: int) -> None:
            if progress:
                progress(
                    OperationProgress.determinate(
                        "speaker_clustering_downloading_model",
                        completed=completed,
                        total=total,
                        unit="bytes",
                    )
                )

        if not model.is_file():
            download_with_resume(
                SPEAKER_CLUSTERING_MODEL_URL,
                model,
                SPEAKER_CLUSTERING_MODEL_SIZE,
                progress=report_download,
                check_cancelled=check_cancelled,
            )
        actual_sha256 = sha256_file(model)
        if actual_sha256 != SPEAKER_CLUSTERING_MODEL_SHA256:
            raise RuntimeError(
                f"3D-Speaker 模型校验失败：{actual_sha256}，预期 {SPEAKER_CLUSTERING_MODEL_SHA256}"
            )
        return {
            "version": SPEAKER_CLUSTERING_VERSION,
            "python": str(python.resolve()),
            "model": str(model.resolve()),
        }

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
            / f"{self.settings.asr.model}-{self.settings.asr.device}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        settings = self.settings.asr.model_copy(update={"cli_path": str(cli_path)})
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
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
