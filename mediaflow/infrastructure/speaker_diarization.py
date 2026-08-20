from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling
from mediaflow.domain.dubbing import (
    DiarizationResult,
    DiarizationSpeechInterval,
    DiarizationTurn,
)
from mediaflow.domain.settings import SpeakerDiarizationSettings

from .subprocess_runner import run_cancellable


def pyannote_environment_root(python_executable: str | Path) -> Path:
    executable = Path(python_executable).expanduser().resolve()
    if executable.parent.name.lower() in {"scripts", "bin"}:
        return executable.parent.parent
    return executable.parent


def pyannote_cache_root(python_executable: str | Path) -> Path:
    return pyannote_environment_root(python_executable) / "cache"


def pyannote_model_ready_marker(
    python_executable: str | Path,
    model: str,
) -> Path:
    digest = hashlib.sha256(model.strip().encode("utf-8")).hexdigest()[:24]
    return pyannote_cache_root(python_executable) / "ready" / f"{digest}.json"


class TranscriptSpeakerClusteringEngine:
    def __init__(
        self,
        settings: SpeakerDiarizationSettings,
        *,
        check_cancelled=None,
    ) -> None:
        if not settings.clustering_python_executable or not settings.embedding_model_path:
            raise FileNotFoundError(
                "请先在设置中安装本地说话人识别；普通音色聚类不需要 Hugging Face 账号"
            )
        self.python = Path(settings.clustering_python_executable).expanduser().resolve()
        self.model = Path(settings.embedding_model_path).expanduser().resolve()
        if not self.python.is_file():
            raise FileNotFoundError(f"说话人识别 Python 不存在：{self.python}")
        if not self.model.is_file():
            raise FileNotFoundError(f"3D-Speaker 模型不存在：{self.model}")
        self.settings = settings
        self.check_cancelled = check_cancelled
        self.script = Path(__file__).resolve().parents[1] / "resources" / "speaker_cluster.py"
        if not self.script.is_file():
            raise FileNotFoundError(self.script)

    def diarize(
        self,
        source: str | Path,
        *,
        speech_intervals: tuple[DiarizationSpeechInterval, ...],
        minimum_speakers: int | None = None,
        maximum_speakers: int | None = None,
    ) -> DiarizationResult:
        if not speech_intervals:
            raise ValueError("音色聚类需要至少一个转写片段")
        ordered = tuple(
            sorted(
                speech_intervals,
                key=lambda item: (item.start_seconds, item.end_seconds),
            )
        )
        if any(
            left.end_seconds > right.start_seconds
            for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError(
                "转写片段存在重叠；普通音色聚类只支持轮流说话，请改用 Community-1"
            )
        media = Path(source).resolve(strict=True)
        request = media.with_name(f"{media.stem}.speaker-clustering.request.json")
        output = media.with_name(f"{media.stem}.speaker-clustering.result.json")
        atomic_write_text(
            request,
            json.dumps(
                {
                    "schema_version": 1,
                    "intervals": [item.model_dump(mode="json") for item in ordered],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        command = [
            str(self.python),
            str(self.script),
            "--input",
            str(media),
            "--request",
            str(request),
            "--output",
            str(output),
            "--model",
            str(self.model),
            "--num-threads",
            str(self.settings.clustering_num_threads),
            "--threshold",
            str(self.settings.clustering_threshold),
        ]
        if minimum_speakers is not None:
            command.extend(["--minimum-speakers", str(minimum_speakers)])
        if maximum_speakers is not None:
            command.extend(["--maximum-speakers", str(maximum_speakers)])
        completed = run_cancellable(
            command,
            check_cancelled=self.check_cancelled,
            env={**os.environ, "PYTHONUTF8": "1"},
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.settings.timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            detail = "\n".join(
                part for part in (completed.stderr, completed.stdout) if part
            ).strip()
            raise RuntimeError(
                "本地音色聚类失败：" + (detail[-4000:] or f"退出码 {completed.returncode}")
            )
        if not output.is_file():
            raise RuntimeError("本地音色聚类没有生成结果文件")
        payload = json.loads(output.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise RuntimeError("本地音色聚类结果版本不受支持")
        turns = tuple(
            DiarizationTurn(
                speaker=str(item["speaker"]),
                start_seconds=float(item["start_seconds"]),
                end_seconds=float(item["end_seconds"]),
            )
            for item in payload.get("turns") or ()
        )
        if len(turns) != len(ordered):
            raise RuntimeError("本地音色聚类没有覆盖全部转写片段")
        if any(
            abs(turn.start_seconds - interval.start_seconds) > 1e-6
            or abs(turn.end_seconds - interval.end_seconds) > 1e-6
            for turn, interval in zip(turns, ordered, strict=True)
        ):
            raise RuntimeError("本地音色聚类改变了转写片段的时间范围")
        return DiarizationResult(
            engine=str(payload.get("engine") or "3D-Speaker CAM++ via sherpa-onnx"),
            engine_version=str(payload.get("engine_version") or "unknown"),
            model=str(payload.get("model") or self.model.name),
            device="cpu",
            exclusive=True,
            turns=turns,
        )


class PyannoteDiarizationEngine:
    def __init__(
        self,
        settings: SpeakerDiarizationSettings,
        *,
        check_cancelled=None,
    ) -> None:
        if not settings.python_executable:
            raise FileNotFoundError(
                "请先在设置中选择安装了 pyannote.audio 的独立 Python 环境"
            )
        self.python = Path(settings.python_executable).expanduser().resolve(strict=True)
        if not self.python.is_file():
            raise FileNotFoundError(self.python)
        self.settings = settings
        self.check_cancelled = check_cancelled
        self.script = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "pyannote_diarize.py"
        )
        if not self.script.is_file():
            raise FileNotFoundError(self.script)

    def diarize(
        self,
        source: str | Path,
        *,
        minimum_speakers: int | None = None,
        maximum_speakers: int | None = None,
    ) -> DiarizationResult:
        media = Path(source).resolve(strict=True)
        output = unique_temporary_sibling(media, label="diarization-json").with_suffix(".json")
        command = [
            str(self.python),
            str(self.script),
            "--input",
            str(media),
            "--output",
            str(output),
            "--model",
            self.settings.model,
            "--device",
            self.settings.device,
        ]
        if minimum_speakers is not None:
            command.extend(["--minimum-speakers", str(minimum_speakers)])
        if maximum_speakers is not None:
            command.extend(["--maximum-speakers", str(maximum_speakers)])
        cache_root = pyannote_cache_root(self.python)
        hugging_face_home = cache_root / "huggingface"
        torch_home = cache_root / "torch"
        hugging_face_home.mkdir(parents=True, exist_ok=True)
        torch_home.mkdir(parents=True, exist_ok=True)
        environment = {
            **os.environ,
            "PYTHONUTF8": "1",
            "HF_HOME": str(hugging_face_home),
            "HF_HUB_CACHE": str(hugging_face_home / "hub"),
            "TORCH_HOME": str(torch_home),
        }
        token = self.settings.hugging_face_token.strip()
        if token:
            environment["HF_TOKEN"] = token
        elif (
            Path(self.settings.model).expanduser().exists()
            or pyannote_model_ready_marker(
                self.python,
                self.settings.model,
            ).is_file()
        ):
            environment["HF_HUB_OFFLINE"] = "1"
        try:
            completed = run_cancellable(
                command,
                check_cancelled=self.check_cancelled,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                detail = "\n".join(
                    part for part in (completed.stderr, completed.stdout) if part
                ).strip()
                raise RuntimeError(
                    "说话人识别失败：" + (detail[-4000:] or f"退出码 {completed.returncode}")
                )
            if not output.is_file():
                raise RuntimeError("说话人识别没有生成结果文件")
            payload = json.loads(output.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1:
                raise RuntimeError("说话人识别结果版本不受支持")
            turns = tuple(
                DiarizationTurn(
                    speaker=str(item["speaker"]),
                    start_seconds=float(item["start_seconds"]),
                    end_seconds=float(item["end_seconds"]),
                )
                for item in payload.get("turns") or ()
            )
            if not turns:
                raise RuntimeError("说话人识别没有找到可用语音区间")
            if any(
                not item.speaker
                or item.start_seconds < 0
                or item.end_seconds <= item.start_seconds
                for item in turns
            ):
                raise RuntimeError("说话人识别返回了无效时间区间")
            result = DiarizationResult(
                engine=str(payload.get("engine") or "pyannote.audio"),
                engine_version=str(payload.get("engine_version") or "unknown"),
                model=str(payload.get("model") or self.settings.model),
                device=str(payload.get("device") or self.settings.device),
                exclusive=bool(payload.get("exclusive", False)),
                turns=turns,
            )
            marker = pyannote_model_ready_marker(
                self.python,
                self.settings.model,
            )
            atomic_write_text(
                marker,
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": self.settings.model,
                        "engine_version": result.engine_version,
                        "device": result.device,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            return result
        finally:
            if output.exists():
                output.unlink()
