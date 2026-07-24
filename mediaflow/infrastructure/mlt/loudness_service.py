from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.process_observers import (
    FfmpegProgressObserver,
    MeltProgressObserver,
    ffmpeg_progress_command,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable_streaming

from .compiler import TimelineCompiler


@dataclass(frozen=True, slots=True)
class LoudnessMetrics:
    sample_peak_dbfs: float
    true_peak_dbtp: float
    short_term_lufs: float
    integrated_lufs: float


class LoudnessAnalysisService:
    """Measure the compiled sequence audio graph with FFmpeg EBU R128 filters."""

    def __init__(self, compiler: TimelineCompiler, paths: RuntimePaths | None = None):
        self.compiler = compiler
        self.paths = paths or RuntimePaths.discover()

    def analyze(
        self,
        state: TimelineState,
        *,
        check_cancelled=None,
        progress=None,
    ) -> tuple[LoudnessMetrics, Path]:
        if self.paths.melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        project_dir = self.compiler.repository.project_dir
        cache_dir = project_dir / "cache" / "audio"
        graph_path = project_dir / "cache" / "mlt" / f"{state.sequence.id}-loudness.mlt"
        rendered_audio = cache_dir / f"{state.sequence.id}-loudness.wav"
        result_path = project_dir / "generated" / "audio" / f"{state.sequence.id}-loudness.json"
        cache_dir.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)

        if progress:
            progress(OperationProgress.indeterminate("audio_analysis_compiling"))
        self.compiler.write(state, graph_path, use_proxies=False)
        environment = os.environ.copy()
        environment.pop("MLT_REPOSITORY_DENY", None)
        mlt_root = self.paths.melt.parent
        environment["MLT_REPOSITORY"] = str(mlt_root / "lib" / "mlt")
        environment["MLT_DATA"] = str(mlt_root / "share" / "mlt")
        total_frames = max(1, state.duration_frames)
        melt_observer = MeltProgressObserver(
            total_frames,
            lambda frame: progress(
                OperationProgress.determinate(
                    "audio_analysis_rendering",
                    completed=frame,
                    total=total_frames,
                    unit="frames",
                )
            )
            if progress
            else None,
        )
        render = run_cancellable_streaming(
            [
                str(self.paths.melt),
                "-progress2",
                str(graph_path),
                "-consumer",
                f"avformat:{rendered_audio}",
                "f=wav",
                "vn=1",
                "acodec=pcm_f32le",
                "ar=48000",
                f"ac={state.sequence.profile.audio_channels}",
                "terminate_on_pause=1",
                "real_time=-1",
            ],
            cwd=mlt_root,
            env=environment,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check_cancelled=check_cancelled,
            on_stdout_line=melt_observer,
            on_stderr_line=melt_observer,
            split_carriage_returns=True,
        )
        if render.returncode != 0 or not rendered_audio.is_file() or rendered_audio.stat().st_size == 0:
            raise RuntimeError("Sequence audio render failed:\n" + (render.stderr or render.stdout or ""))
        if progress:
            progress(
                OperationProgress.determinate(
                    "audio_analysis_rendering",
                    completed=total_frames,
                    total=total_frames,
                    unit="frames",
                )
            )

        loudness_log = self._ffmpeg_measure(
            rendered_audio,
            "ebur128=peak=true:framelog=verbose",
            check_cancelled,
            loglevel="verbose",
            message_code="audio_analysis_measuring_loudness",
            duration_seconds=total_frames / state.sequence.profile.fps,
            progress=progress,
        )
        peak_log = self._ffmpeg_measure(
            rendered_audio,
            "astats=metadata=0:reset=0",
            check_cancelled,
            loglevel="info",
            message_code="audio_analysis_measuring_peak",
            duration_seconds=total_frames / state.sequence.profile.fps,
            progress=progress,
        )
        metrics = LoudnessMetrics(
            sample_peak_dbfs=self._sample_peak(peak_log),
            true_peak_dbtp=self._last_metric(loudness_log, r"Peak:\s*([^\s]+)\s+dBFS"),
            short_term_lufs=self._short_term(loudness_log),
            integrated_lufs=self._last_metric(loudness_log, r"I:\s*([^\s]+)\s+LUFS"),
        )
        temporary = result_path.with_suffix(result_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    **asdict(metrics),
                    "sequence_id": state.sequence.id,
                    "source_graph": str(graph_path.relative_to(project_dir).as_posix()),
                    "rendered_audio": str(rendered_audio.relative_to(project_dir).as_posix()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(result_path)
        if progress:
            progress(OperationProgress.indeterminate("audio_analysis_saving"))
        return metrics, result_path

    def _ffmpeg_measure(
        self,
        source: Path,
        audio_filter: str,
        check_cancelled,
        *,
        loglevel: str,
        message_code: str,
        duration_seconds: float,
        progress=None,
    ) -> str:
        command = [
                str(self.paths.ffmpeg),
                "-hide_banner",
                "-nostats",
                "-loglevel",
                loglevel,
                "-i",
                str(source),
                "-af",
                audio_filter,
                "-f",
                "null",
                "-",
            ]
        observer = FfmpegProgressObserver(
            duration_seconds,
            lambda position: progress(
                OperationProgress.determinate(
                    message_code,
                    completed=position,
                    total=duration_seconds,
                    unit="media_seconds",
                )
            )
            if progress
            else None,
        )
        result = run_cancellable_streaming(
            ffmpeg_progress_command(command),
            on_stderr_line=observer,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check_cancelled=check_cancelled,
        )
        if result.returncode != 0:
            raise RuntimeError("Audio measurement failed:\n" + (result.stderr or result.stdout or ""))
        return "\n".join(part for part in (result.stdout, result.stderr) if part)

    @classmethod
    def _sample_peak(cls, output: str) -> float:
        values = cls._metrics(output, r"Peak level dB:\s*([^\s]+)")
        if not values:
            raise RuntimeError("FFmpeg astats did not report a sample peak")
        return max(values)

    @classmethod
    def _short_term(cls, output: str) -> float:
        values = cls._metrics(output, r"\bS:\s*([^\s]+)")
        if not values:
            raise RuntimeError("FFmpeg ebur128 did not report short-term loudness")
        return max(values)

    @classmethod
    def _last_metric(cls, output: str, pattern: str) -> float:
        values = cls._metrics(output, pattern)
        if not values:
            raise RuntimeError(f"FFmpeg did not report metric: {pattern}")
        return values[-1]

    @staticmethod
    def _metrics(output: str, pattern: str) -> list[float]:
        values: list[float] = []
        for token in re.findall(pattern, output, flags=re.IGNORECASE):
            try:
                value = float(token)
            except ValueError:
                continue
            if math.isfinite(value):
                values.append(value)
        return values
