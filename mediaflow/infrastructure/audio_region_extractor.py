from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from mediaflow.infrastructure.audio_chunking import AudioPreparationService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable


class FfmpegAudioRegionExtractor:
    def __init__(self, paths: RuntimePaths):
        self.paths = paths

    def extract(
        self,
        media_path: str | Path,
        output_path: str | Path,
        *,
        start_seconds: float,
        duration_seconds: float,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Path:
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        source = AudioPreparationService(self.paths).prepare_for_asr(
            media_path,
            check_cancelled=check_cancelled,
        )
        result = run_cancellable(
            [
                str(self.paths.ffmpeg),
                "-y",
                "-hide_banner",
                "-v",
                "error",
                "-ss",
                f"{start_seconds:.6f}",
                "-i",
                str(source),
                "-t",
                f"{duration_seconds:.6f}",
                "-vn",
                "-c:a",
                "copy",
                str(output),
            ],
            check_cancelled=check_cancelled,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            detail = str(result.stderr or "").strip() or "FFmpeg 没有生成音频片段"
            raise RuntimeError(f"提取转录选区失败：{detail}")
        if check_cancelled:
            check_cancelled()
        return output
