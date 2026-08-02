from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling


@dataclass(frozen=True, slots=True)
class GptSoVitsResult:
    output_path: Path
    sha256: str
    duration_seconds: float
    sample_rate: int
    channels: int
    reference_audio_sha256: str
    device: Literal["cuda", "cpu"]


class GptSoVitsEngine:
    def __init__(
        self,
        root: str | Path,
        runtime_dir: str | Path,
        *,
        device: str = "auto",
        startup_timeout_seconds: int = 300,
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        self.root = Path(root).resolve(strict=True)
        self.runtime_dir = Path(runtime_dir).resolve()
        self.device = self._resolve_device(device)
        self.startup_timeout_seconds = startup_timeout_seconds
        self.check_cancelled = check_cancelled
        self.python = self.root / "runtime" / "python.exe"
        self.api = self.root / "api_v2.py"
        for required in (self.python, self.api):
            if not required.is_file():
                raise FileNotFoundError(f"GPT-SoVITS 安装不完整：{required}")

    def synthesize(
        self,
        *,
        text: str,
        text_language: str,
        reference_audio: str | Path,
        reference_text: str,
        reference_language: str,
        output_path: str | Path,
        auxiliary_reference_audio: list[str | Path] | None = None,
        speed_factor: float = 1.0,
        seed: int = -1,
        timeout_seconds: int = 900,
        overwrite: bool = False,
    ) -> GptSoVitsResult:
        reference = Path(reference_audio).resolve(strict=True)
        auxiliaries = [Path(item).resolve(strict=True) for item in auxiliary_reference_audio or ()]
        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() != ".wav":
            raise ValueError("GPT-SoVITS 公共操作只输出 WAV")
        if output.exists() and not overwrite:
            raise FileExistsError(f"输出已存在：{output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        config = self._write_config()
        port = self._available_port()
        command = [
            str(self.python),
            str(self.api),
            "-a",
            "127.0.0.1",
            "-p",
            str(port),
            "-c",
            str(config),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            cwd=self.root,
            env={**os.environ, "PYTHONUTF8": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        captured: list[str] = []
        reader = threading.Thread(
            target=self._consume_output,
            args=(process, captured),
            name="mediaflow-gpt-sovits-output",
            daemon=True,
        )
        reader.start()
        temporary = unique_temporary_sibling(output, label="gpt-sovits")
        try:
            self._wait_until_ready(process, port, captured)
            payload = {
                "text": text,
                "text_lang": text_language,
                "ref_audio_path": str(reference),
                "aux_ref_audio_paths": [str(item) for item in auxiliaries],
                "prompt_text": reference_text,
                "prompt_lang": reference_language,
                "speed_factor": speed_factor,
                "seed": seed,
                "media_type": "wav",
                "streaming_mode": False,
            }
            request = Request(
                f"http://127.0.0.1:{port}/tts",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=timeout_seconds) as response:
                    content_type = str(response.headers.get("Content-Type") or "")
                    if "audio/" not in content_type:
                        detail = response.read().decode("utf-8", errors="replace")
                        raise RuntimeError(f"GPT-SoVITS 返回了非音频结果：{detail}")
                    with temporary.open("xb") as destination:
                        while chunk := response.read(1024 * 1024):
                            if self.check_cancelled:
                                self.check_cancelled()
                            destination.write(chunk)
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GPT-SoVITS 合成失败（HTTP {error.code}）：{detail}"
                ) from error
            except URLError as error:
                raise RuntimeError(f"GPT-SoVITS 本地接口不可用：{error}") from error
            sample_rate, channels, duration = self._inspect_wave(temporary)
            temporary.replace(output)
            return GptSoVitsResult(
                output_path=output,
                sha256=self._sha256(output),
                duration_seconds=duration,
                sample_rate=sample_rate,
                channels=channels,
                reference_audio_sha256=self._sha256(reference),
                device=self.device,
            )
        finally:
            if temporary.exists():
                temporary.unlink()
            self._stop(process)
            reader.join(timeout=2)

    def _write_config(self) -> Path:
        is_half = self.device == "cuda"
        pretrained = self.root / "GPT_SoVITS" / "pretrained_models"
        values = {
            "custom": {
                "bert_base_path": str(pretrained / "chinese-roberta-wwm-ext-large"),
                "cnhuhbert_base_path": str(pretrained / "chinese-hubert-base"),
                "device": self.device,
                "is_half": is_half,
                "t2s_weights_path": str(pretrained / "s1v3.ckpt"),
                "version": "v2Pro",
                "vits_weights_path": str(pretrained / "v2Pro" / "s2Gv2Pro.pth"),
            }
        }
        path = self.runtime_dir / "cache" / "gpt-sovits" / f"v2pro-{self.device}.yaml"
        atomic_write_text(path, json.dumps(values, ensure_ascii=False, indent=2))
        return path

    def _wait_until_ready(
        self,
        process: subprocess.Popen[str],
        port: int,
        captured: list[str],
    ) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.check_cancelled:
                self.check_cancelled()
            if process.poll() is not None:
                detail = "\n".join(captured[-40:]).strip()
                raise RuntimeError(
                    f"GPT-SoVITS 启动失败（{process.returncode}）：{detail or '没有进程输出'}"
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    return
            except OSError:
                time.sleep(0.25)
        detail = "\n".join(captured[-40:]).strip()
        raise TimeoutError(
            f"GPT-SoVITS 在 {self.startup_timeout_seconds} 秒内没有启动：{detail}"
        )

    @staticmethod
    def _consume_output(process: subprocess.Popen[str], captured: list[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            captured.append(line.rstrip())
            if len(captured) > 200:
                del captured[:100]

    @staticmethod
    def _stop(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    @staticmethod
    def _inspect_wave(path: Path) -> tuple[int, int, float]:
        try:
            with wave.open(str(path), "rb") as source:
                sample_rate = source.getframerate()
                channels = source.getnchannels()
                frames = source.getnframes()
        except (OSError, wave.Error) as error:
            raise RuntimeError(f"GPT-SoVITS 没有生成可用的 WAV：{error}") from error
        if sample_rate <= 0 or channels <= 0 or frames <= 0:
            raise RuntimeError("GPT-SoVITS 生成的 WAV 没有音频帧")
        return sample_rate, channels, frames / sample_rate

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(4 * 1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    @staticmethod
    def _resolve_device(value: str) -> Literal["cuda", "cpu"]:
        if value not in {"auto", "cuda", "cpu"}:
            raise ValueError(f"不支持的 GPT-SoVITS 设备：{value}")
        if value == "auto":
            return "cuda" if shutil.which("nvidia-smi") else "cpu"
        return "cuda" if value == "cuda" else "cpu"
